# /bin/sh

if [ ! -d "venv" ]; then 
    virtualenv venv -p python3
    source venv/bin/activate
    pip install django
    pip install djangogrpcframework
    pip install djangorestframework
    pip install grpcio
    pip install grpcio-tools
    pip install django-mathfilters
fi

source venv/bin/activate

./generate-proto.sh

python manage.py makemigrations
python manage.py migrate --run-syncdb