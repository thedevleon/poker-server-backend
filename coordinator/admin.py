from django.contrib import admin

from coordinator.models import Game, GameAdmin, Player

# Register your models here.
admin.site.register(Player)
admin.site.register(Game, GameAdmin)
