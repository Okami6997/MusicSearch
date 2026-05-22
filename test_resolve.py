import requests
from flask import Flask, json
from werkzeug.test import Client
from werkzeug.test import EnvironBuilder

# Create a minimal Flask app with the resolve route
app = Flask(__name__)

@app.route('/api/resolve', methods=['GET'])
def resolve():
    return json.dumps({'name': 'resolve', 'arguments': {'mode': 'sync', 'timeout': 30000}})

# Test with the three URLs
urls = [
    'https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl',
    'https://music.apple.com/us/song/blinding-lights/1487958573',
    'https://soundcloud.com/odesza/line-of-sight'
]

for url in urls:
    print(f"=== Testing {url} ===")
    try:
        builder = EnvironBuilder(
            method='GET',
            path=f'/api/resolve?url={url}',
            headers={'Host': 'localhost:5000'}
        )
        client = Client(app)
        response = client.get(builder)
        print(f"Status: {response.status_code}", response.json())
    except Exception as e:
        print(f"Error: {str(e)}")
