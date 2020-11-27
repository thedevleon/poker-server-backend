import threading
import queue
import random

from typing import List

from coordinator.games.kuhn_game import KuhnRootChanceGameState
from coordinator.games.kuhn_constants import CARDS_DEALINGS, NEXT
from coordinator.models import Game


class KuhnGameLobbyStageMessage(object):

    def __init__(self, state, actions):
        self.state = state
        self.actions = actions


class KuhnGameLobbyStageError(object):

    def __init__(self, error):
        self.error = error


class KuhnGameLobbyPlayerMessage(object):

    def __init__(self, player_id, action):
        self.player_id = player_id
        self.action = action


class KuhnGameLobbyPlayer(object):

    def __init__(self, player_id: int, bank: int):
        self.player_id = player_id
        self.bank = bank
        self.channel = queue.Queue()

    def send_message(self, message: KuhnGameLobbyStageMessage):
        self.channel.put(message)

    def send_error(self, error: KuhnGameLobbyStageError):
        self.channel.put(error)


class KuhnGameLobbyStage(object):

    def __init__(self, lobby):
        self._root = KuhnRootChanceGameState(CARDS_DEALINGS)
        self._stage = self._root.play(random.choice(CARDS_DEALINGS))
        self._cards = self._stage.cards

    def cards(self):
        return self._cards

    def card(self, index):
        return self._cards[index]

    def actions(self):
        return self._stage.actions

    def play(self, action):
        self._stage = self._stage.play(action)

    def is_terminal(self):
        return self._stage.is_terminal()

    def inf_set(self):
        return self._stage.inf_set()

    def secret_inf_set(self):
        return self._stage.secret_inf_set()


class KuhnGameRound(object):

    def __init__(self, lobby):
        self.lobby = lobby
        self.stage = KuhnGameLobbyStage(lobby)
        self.first_player = lobby.get_random_player_id()
        self.player_id_turn = self.first_player
        self.started = {}


class KuhnGameLobby(object):
    InitialBank = 5
    MessagesTimeout = 5

    def __init__(self, game_id: str):
        self.lock = threading.Lock()
        self.game_id = game_id
        self.rounds = []
        self.channel = queue.Queue()

        # private fields
        self._closed = threading.Event()
        self._lobby_coordinator_thread = None
        self._player_connection_barrier = threading.Barrier(3)

        self._players = {}
        self._player_opponent = {}

    def close(self):
        with self.lock:
            self._closed.set()

    def is_closed(self) -> bool:
        with self.lock:
            return self._closed.is_set()

    def get_players(self) -> List[KuhnGameLobbyPlayer]:
        return list(self._players.values())

    def get_player(self, player_id: str) -> KuhnGameLobbyPlayer:
        return self._players[player_id]

    def get_player_ids(self) -> List[str]:
        return list(self._players.keys())

    def get_num_players(self) -> int:
        return len(self.get_player_ids())

    def get_player_opponent(self, player_id: str) -> str:
        return self._player_opponent[player_id]

    def get_player_channel(self, player_id: str) -> queue.Queue:
        return self._players[player_id].channel

    def get_random_player_id(self) -> str:
        return random.choice(list(self._players.keys()))

    def start(self):
        # First player which hits this function starts a separate thread with a game coordinator
        # Ref: play_lobby(lobby)
        with self.lock:
            if self._lobby_coordinator_thread is None:
                self._lobby_coordinator_thread = threading.Thread(target = game_lobby_coordinator,
                                                                  args = (self, KuhnGameLobby.MessagesTimeout))
                self._lobby_coordinator_thread.start()

    def register_player(self, player_id: int):
        with self.lock:
            # Check if lobby is already full or throw an exception otherwise
            if self.get_num_players() >= 2:
                raise Exception('Game lobby is full')

            # For each player we create a separate channel for messages between game coordinator and player
            self._players[player_id] = KuhnGameLobbyPlayer(player_id, bank = KuhnGameLobby.InitialBank)

            # If both players are connected we set corresponding ids to self._player_opponent dictionary for easy lookup
            if self.get_num_players() == 2:
                player_ids = self.get_player_ids()
                player1_id, player2_id = player_ids[0], player_ids[1]

                self._player_opponent[player1_id] = player2_id
                self._player_opponent[player2_id] = player1_id

                # Update database entry of the game with corresponding player ids and mark it as started
                game_db = Game.objects.get(id = self.game_id)
                game_db.player_1 = player1_id
                game_db.player_2 = player2_id
                game_db.is_started = True
                game_db.save(update_fields = ['player_1', 'player_2', 'is_started'])

    def wait_for_players(self):
        try:
            self._player_connection_barrier.wait(timeout = 120)
        except threading.BrokenBarrierError:
            raise Exception('Timeout waiting for another player to connect')

    def get_last_round(self):
        return self.rounds[-1] if len(self.rounds) >= 1 else None

    def create_new_round(self):
        last_round = self.get_last_round()
        if last_round is None or last_round.stage.is_terminal():
            _round = KuhnGameRound(self)
            self.rounds.append(_round)
            return _round
        else:
            raise Exception('It is not allowed to start a new round while previous one is not completed')

    def start_new_round(self, player_id):
        last_round = self.get_last_round()
        if player_id in last_round.started and last_round.started[player_id] is True:
            return
        player = self.get_player(player_id)
        last_round.started[player_id] = True
        if player.player_id == last_round.player_id_turn:
            player.send_message(KuhnGameLobbyStageMessage(f'{last_round.stage.card(0)}', last_round.stage.actions()))
        else:
            player.send_message(KuhnGameLobbyStageMessage(f'{last_round.stage.card(1)}', ['WAIT']))


def game_lobby_coordinator(lobby: KuhnGameLobby, messages_timeout: int):
    lobby.wait_for_players()

    try:
        current_round = lobby.create_new_round()

        for player in lobby.get_players():
            lobby.start_new_round(player.player_id)

        games_counter = 1

        while not lobby.is_closed() or not lobby.channel.empty():
            try:
                message = lobby.channel.get(timeout = messages_timeout)

                print(f'Received a message: {message}')

                if message.action == 'START':
                    if games_counter < 5:
                        lobby.start_new_round(message.player_id)
                    else:
                        for player in lobby.get_players():
                            player.send_message(KuhnGameLobbyStageMessage('DEFEAT', []))
                        lobby.close()
                elif message.player_id == current_round.player_id_turn:
                    current_round.stage.play(message.action)
                    if current_round.stage.is_terminal():
                        for player in lobby.get_players():
                            player.send_message(KuhnGameLobbyStageMessage(f'END:{current_round.stage.inf_set()}', ['START']))
                        current_round = lobby.create_new_round()
                        games_counter = games_counter + 1
                    else:
                        current_round.player_id_turn = lobby.get_player_opponent(current_round.player_id_turn)
                        lobby.get_player(current_round.player_id_turn).send_message(
                            KuhnGameLobbyStageMessage(current_round.stage.secret_inf_set(), current_round.stage.actions())
                        )
                elif message.action == 'WAIT':
                    continue
                else:
                    print(f'Warn: unexpected message: {message}')
                    continue

            except queue.Empty:
                raise Exception(f'There was no message from a player for more than {messages_timeout} sec.')

    except Exception as e:
        for player in lobby.get_players():
            # noinspection PyBroadException
            try:
                print(f'Exception: {e}')
                player.send_error(KuhnGameLobbyStageError(e))
            except Exception:
                pass
