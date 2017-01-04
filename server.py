# Copyright 2015 IBM Corp. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import requests
from flask import Flask, jsonify
from cloudant.account import Cloudant
from watson_developer_cloud import PersonalityInsightsV2 as PersonalityInsights
from watson_developer_cloud import ToneAnalyzerV3Beta 
from twitter import *
from scipy.spatial import distance

import sys
reload(sys)
sys.setdefaultencoding('utf8')

if 'TWITTER_CREDS' not in os.environ:
    raise RuntimeError('TWITTER_CREDS not found.')
else:
    TWITTER = json.loads(os.environ['TWITTER_CREDS'])

twitter = Twitter(auth = OAuth(TWITTER["4765604906-gSTFzNcT5wmbCIXgEKLuWsVKoBVSlK5j57OO4NR"], TWITTER["ESa0nHlXWAnYe7PGykRX562hYRgwMgwlxvnFX2gHhJcD4"], TWITTER["UN7ZLhxdIwIJscNoZhwpRBWNi"], TWITTER["UI5Oz6GubwsizjepDCTbh3TAQaXNyHjehfczcpDcAItpMqu76A"]))
if 'VCAP_SERVICES' not in os.environ:
    raise RuntimeError("VCAP_SERVICES not found.")
elif 'cloudantNoSQLDB' not in json.loads(os.environ['VCAP_SERVICES']):
    raise RuntimeError("Cloudant database not bound to service.")

WATSON = json.loads(os.environ['VCAP_SERVICES'])['personality_insights'][0]
if 'credentials' not in WATSON:
    raise RuntimeError("Cannot connect to Watson.  Credentials not found for personality insights.")
else:
    personality_insights = PersonalityInsights(username=WATSON['credentials']['username'], password=WATSON['credentials']['password'])
    
TONE = json.loads(os.environ['VCAP_SERVICES'])['tone_analyzer'][0]
if 'credentials' not in TONE:
    raise RuntimeError("Cannot connect to Watson.  Credentials not found for personality insights.")
else:
    tone_analyzer = ToneAnalyzerV3Beta(username=TONE['credentials']['username'], password=TONE['credentials']['password'], version='2016-02-11')
    

CLOUDANT = json.loads(os.environ['VCAP_SERVICES'])['cloudantNoSQLDB'][0]
if 'credentials' not in CLOUDANT:
    raise RuntimeError("Cannot connect to database, Cloudant credentials not found.")
else:
    client = Cloudant(CLOUDANT['credentials']['username'], CLOUDANT['credentials']['password'], url=CLOUDANT['credentials']['url'])
    client.connect()

databases = ['personas', 'albums', 'songs']
for db in databases:
    if db not in client.all_dbs():
        raise RuntimeError("Database " + db + " not found, please ensure you have the needed data.")

cached_tone = {}
for persona in client['personas']:
    cached_tone[persona['_id']] = None

cached_persona_insights = {}
for persona in client['personas']:
    cached_persona_insights[persona['_id']] = None

def assemble_persona_text(persona):
    text = ''
    for album in client['personas'][persona]['albums']:
        for song in client['albums'][album['title']]['songs']:            
            try:
                if 'lyrics' in client['songs'][song]:
                    text += client['songs'][song]['lyrics']
            except KeyError as e:
                print e  #just swallow it silently for now ToDo: something better...
    return text

def pull_tweets_by_screenname(screenname):
    tweets = response = twitter.statuses.user_timeline(screen_name = screenname, count = 200)
    while len(response) > 0:
        print 'fetching more tweets for ' + screenname
        response = twitter.statuses.user_timeline(screen_name = screenname, count = 200, max_id = tweets[-1]['id'] - 1)
        tweets.extend(response)
    print 'total of ' + str(len(tweets)) + ' found for user ' + screenname
    return tweets

def aggregate_tweet_string(tweets):
    aggregate_text = ''
    for tweet in tweets:
        aggregate_text += tweet['text'] + "\n"
    return aggregate_text

# returns an object like {'Openness': 0.5235988, 'Extraversion': 0.511561636, ... etc ... }
def extract_big5_scores(insights_response):
    traits = {}
    # pull out the "big5" personality traits from the profile
    for trait in insights_response['tree']['children'][0]['children'][0]['children']:
        traits[trait['name']] = trait['percentage']
    return traits

# build ordered tuples from the values of the big5 traits
def build_comparison_tuple(traits):
    tuple = ()
    for trait in sorted(traits.keys()):
        tuple = tuple + (traits[trait],)
    return tuple

def calculate_personality_distance(twitter_profile):
    output = {'twitter': extract_big5_scores(twitter_profile)}
    twitter_tuple = build_comparison_tuple(output['twitter'])

    # run through all the personas and calculate Euclidean distance from the twitter profile
    for persona in cached_persona_insights:
        if cached_persona_insights[persona] is not None:
            output[persona] = extract_big5_scores(cached_persona_insights[persona]) # store the raw data to be returned alone with similarity metrics
            persona_tuple = build_comparison_tuple(output[persona])
            output[persona]['distance'] = distance.euclidean(twitter_tuple, persona_tuple)

    return output

## Begin Flask server
app = Flask(__name__)
if 'FLASK_DEBUG' in os.environ:
    app.debug = True

@app.route('/')
def Welcome():
    return app.send_static_file('index.html')

@app.route('/init')
def Initialize():
    print 'this is a test' ;
    
    with open('static/personas.json') as json_file:
        json_data = json.load(json_file)
        # print json_data 
        
        for p in json_data['results']:
            client['personas'].create_document(p)

        print 'loaded file' ;
    return

@app.route('/setup')
def Setup():
    personas = GetPersonas()
    for persona in json.loads(personas.data)['results']:
        print 'Getting persona ' + persona['name']
        GetPersona(persona['name'])
    return 'Setup complete!'

@app.route('/api/personas')
def GetPersonas():
    response = []
    
    for persona in client['personas']:
        
        albums = []
        
        for album in persona['albums']:
            albums.append(album['title']);
        
        response.append({'name': persona['_id'], 'albums': albums})

    return jsonify(results=response)

@app.route('/api/persona/<persona>')
def GetPersona(persona):
        
    if cached_persona_insights[persona] is None:
        
        personality = assemble_persona_text(persona)
        
        insight = personality_insights.profile(json.dumps({'text':personality, 'contenttype': 'text/html'}))
        cached_persona_insights[persona] = insight
    else:
        insight = cached_persona_insights[persona]

    return jsonify(results=insight)

@app.route('/api/tone/<persona>')
def GetTone(persona):
        
    if cached_tone[persona] is None:
        
        personality = assemble_persona_text(persona)
        
        insight = tone_analyzer.tone(json.dumps({'text':personality, 'contenttype': 'text/html'}))
        cached_tone[persona] = insight
    else:
        insight = cached_tone[persona]

    return jsonify(results=insight)


@app.route('/api/collected')
def Collected():
    for persona in client['personas']:
        
        print persona
        
        if cached_persona_insights[persona] is not None:
            insight = cached_persona_insights[persona]
            print insight
            
    return

@app.route('/api/twitter/<screenname>')
def InsightsFromTwitter(screenname):
    tweets = pull_tweets_by_screenname(screenname)
    insight = personality_insights.profile(json.dumps({'text': aggregate_tweet_string(tweets)}, indent=2))
    return jsonify(results=calculate_personality_distance(insight))

port = os.getenv('PORT', '5000')
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(port))
