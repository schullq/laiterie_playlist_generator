from cgitb import html
import argparse, inspect, json, time, requests, re, spotipy, configparser, logging, sys
from fileinput import filename
from glob import glob
from pydoc import doc
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from datetime import datetime
from spotipy.oauth2 import SpotifyOAuth

class LaiterieScrapper:
    laiterie_url = 'https://www.artefact.org/la-laiterie/programmation'
    
    def scrap_url(self, pager):
        html_text = handle_http_request('GET', self.laiterie_url + '?p=' + str(pager)).text
        global soup
        soup = BeautifulSoup(html_text, 'html.parser')
        if not soup.find_all(class_="liste-wp"):
            return False
        return True

    def get_artists(self):
        logging.info("Scrapping artists...")    
        pager = 1
        artists = []
        while (self.scrap_url(pager)):
            pager += 1
            for artist in soup.find_all(class_='artiste tete-affiche'):
                p = artist.find_parent(class_='event-block with-image table-row') 
                s = p.find(class_='statut')
                if not p or not s or 'Annulé' not in s.text:
                    artists.append((re.sub(
                        '(?i)(([\"«].*[\"»])|(\+{1})|([«\"\'(\-]{1}[a-zA-Z0-9 ]*tour[a-zA-Z0-9 ]*[»\"\'(\-]?)|(-[ a-zA-Z0-9)]*))', "", artist.text)).strip())                    
        return artists

class Spotify:
    base_url = 'https://api.spotify.com/v1/'
    playlist_url = base_url + 'playlists/0djk4ksDSzklafJj2z1D4F/tracks'
    scopes = 'playlist-modify-public playlist-modify-private'

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.authenticate()

    def authenticate(self):
        global token_info, sp
        sp_oauth = SpotifyOAuth(client_id=self.client_id,client_secret=self.client_secret,redirect_uri='http://example.com',scope=self.scopes)
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            auth_url = sp_oauth.get_authorize_url()
            response = input('Paste the above link into your browser, then paste the redirect url here: ')
            code = sp_oauth.parse_response_code(response)
            token_info = sp_oauth.get_access_token(code)
        self.token = token_info['access_token']
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        sp = spotipy.Spotify(auth=self.token)

    def refresh_token(self):
        logging.info("Refreshing token...")
        sp_oauth = SpotifyOAuth(client_id=self.client_id,client_secret=self.client_secret,redirect_uri='http://example.com',scope=self.scopes)
        token_info = sp_oauth.get_cached_token()
        if SpotifyOAuth.is_token_expired(token_info):
            token_info = SpotifyOAuth.refresh_access_token(token_info['refresh_token'])
            self.token = token_info['access_token']
            self.headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }

    def search_artist(self, name):
        logging.info(f'Searching for {name}...')
        url = self.base_url + f'search?q={name}&type=artist&include_external=audio'
        response = handle_http_request('GET', url, headers=self.headers)
        if int(response.json().get('artists').get('total')) == 0:
            logging.info(f'No results for {name}')
            return            
        best_match = ('', 0)
        for artist in response.json().get('artists').get('items'):
            ratio = SequenceMatcher(None, artist.get('name').upper(), name.upper()).ratio()
            if ratio > best_match[1]:
                best_match = (artist, ratio)
        if best_match[1] < 0.95:
            f_best_match = best_match
            name = re.sub('(?i)((live)|(dj set)|(\([a-zA-Z ]+\))|( (\&|et|and).+)|[^0-9a-zÀ-ÿ ]+)', '', name).strip()
            for artist in response.json().get('artists').get('items'):
                if name.upper() == artist.get('name').upper():
                    best_match = (artist, SequenceMatcher(None, artist.get('name').upper(), name.upper()).ratio())
                    break
            if f_best_match == best_match:
                if f_best_match == best_match:
                    logging.info(f'No fine match for {name}')
                    return []
        return best_match[0].get('href')

    def get_artist_top_tracks(self, artist_url, amount=1):
        top_tracks_url = artist_url + '/top-tracks?market=FR'
        response = handle_http_request('GET', top_tracks_url, headers=self.headers)
        tracks = response.json().get('tracks')
        return [x.get('uri') for x in tracks[0:amount]]

    def get_last_release_top_tracks(self, artist_url, offset = 0, amount=1):
        last_release_url = artist_url + '/albums?include_groups=album,single&market=FR&limit=50'
        response = handle_http_request('GET', last_release_url, headers=self.headers)
        albums = response.json().get('items')
        if not albums:
            return []
        for album in albums:
            if album.get('release_date_precision') == 'year':
                album['release_date'] = '{}-01-01'.format(album.get('release_date'))
            elif album.get('release_date_precision') == 'month':
                album['release_date'] = '{}-01'.format(album.get('release_date'))
        sorted_albums = sorted(albums, key=lambda x: datetime.strptime(x['release_date'], '%Y-%m-%d'), reverse = True)
        while sorted_albums[0].get('total_tracks') <= offset:
            sorted_albums.pop(0)
            offset -= 1
        response = handle_http_request('GET', sorted_albums[0].get('href'), headers=self.headers)
        tracklist = [x.get('id') for x in response.json().get('tracks').get('items')]
        track_url = f'{self.base_url}tracks?ids={",".join(tracklist)}'
        response = handle_http_request('GET', track_url, self.headers)
        tracks = [(x.get('uri'), x.get('popularity')) for x in response.json().get('tracks')]
        tracks.sort(key=lambda x: x[1], reverse = True)
        return [x[0] for x in tracks[offset:offset+amount]]

    def get_current_tracks(self):
        current_tracks = handle_http_request('GET', self.playlist_url, headers=self.headers).json().get('items')
        return [x.get('track').get('uri') for x in current_tracks]

    def add_track_to_playlist(self, track_uris, force=False):
        logging.info("Adding tracks to playlist...")    
        if not force:
            current_track_uris = self.get_current_tracks()
            for current_track_uri in current_track_uris:
                if current_track_uri in track_uris:
                    track_uris.remove(current_track_uri)
        if track_uris:
            data = json.dumps({
              "uris" : track_uris
            })
            handle_http_request('POST', self.playlist_url, data=data, headers=self.headers)
    
    def clear_past_shows(self, track_uris):
        logging.info("Clearing past shows...")    
        current_track_uris = self.get_current_tracks()
        uris_to_delete = []
        for current_track_uri in current_track_uris:
            if current_track_uri not in track_uris:
                uris_to_delete.append(current_track_uri)
        if uris_to_delete:
            data = json.dumps({
                  "uris" : uris_to_delete
            })
            handle_http_request('DELETE', self.playlist_url, headers=self.headers, data=data)

count_call = 0

def handle_http_request(type, url, headers={}, data={}):
    global count_call
    if count_call != 0 and count_call % 50 == 0:
        logging.info(f'Pausing script 10 sec to avoid 429 (total count: {count_call})...')
        time.sleep(10)
    if type == 'GET':
        response = requests.get(url, headers=headers)
    elif type == 'POST':
        response = requests.post(url, headers=headers, data=data)
    elif type == 'PUT':
        response = requests.put(url, headers=headers, data=data)
    elif type == 'DELETE':
        response = requests.delete(url, headers=headers, data=data)
    count_call += 1
    if not response.ok:
        frm = inspect.stack()[1]
        logging.error(f'HttpError: "({response.status_code}) {response.reason}"  at line {frm[2]} <{frm[3]}>')
        exit()
    return response

def main():
    config = configparser.ConfigParser()
    try:
        with open('./.conf') as f:
            config.read_file(f)
    except IOError:
        raise Exception('.conf file doesn \'t exist')

    log_level = config['logging']['level']
    logging.basicConfig(level=getattr(logging, log_level), filename='.log', filemode='w', format='%(asctime)s - %(levelname)s: %(message)s')

    spotify = Spotify(config['spotify']['client_id'], config['spotify']['client_secret'])
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--refresh-token", help = "Refresh spotify access token.", action="store_true")
    args = parser.parse_args()

    logging.info(f"New script exec with arg {sys.argv[1:]}")

    if args.refresh_token:
        spotify.refresh_token()
    else:
        ls = LaiterieScrapper()
        artists = ls.get_artists()
        tracks = []
        for artist in artists:
            artist_url = spotify.search_artist(artist)
            if artist_url:
                top_tracks = spotify.get_artist_top_tracks(artist_url)
                last_release_top_tracks = []
                offset = 0
                while not last_release_top_tracks:
                    found_tracks = spotify.get_last_release_top_tracks(artist_url, offset)
                    if not found_tracks:
                        break
                    if not any(check in found_tracks for check in top_tracks):
                        last_release_top_tracks = found_tracks
                    else:
                        offset += 1
                tracks.extend(top_tracks)
                tracks.extend(last_release_top_tracks)
        spotify.clear_past_shows(tracks)
        spotify.add_track_to_playlist(tracks)

if __name__=="__main__":
    main()