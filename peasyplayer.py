import logging
import os
import random
import sys
import time
from collections import defaultdict

import rfidpeasyplayer
import vlc
from gpiozero import Button
from pirc522 import RFID

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


class PeasyPlayer:
    movies_dir = '/media/usb/'
    default_dir = '/home/pi/peasyplayer/'

    tick_rate = 10

    movie_formats = ['.mp4', '.mov', '.avi', '.mkv', '.m4v', '.mp3', '.wav']

    def __init__(self):
        self.instance = vlc.Instance()
        self.list_player = self.instance.media_list_player_new()
        self.list_player_media_player = self.list_player.get_media_player()
        self.list_player_media_player.set_fullscreen(True)
        self.reader = RFID()

        # Records last input times for different input types
        self.last_input_times = defaultdict(float)
        self.play_pause_was_held = False  # Tracks if play/pause was held on last input
        self.set_input_listeners()

        self.last_action_time = time.time()
        # How long to wait, in minutes, after last action to stop playlist (and go back to default)
        self.inactive_timeout = 300

    def start(self):
        ''' Main player loop '''
        try:
            logging.info('\n\n\n***** Begin Player *****\n\n\n')
            while True:
                tick_start = time.time()

                # Inactivity timeout. Stops playlist after current movie ends
                if self.last_action_time + self.inactive_timeout*60 < tick_start:
                    logging.info(f'Inactivity timeout: You have been inactive for {self.inactive_timeout} minutes. Resetting player.')
                    self.list_player.set_media_list(self.instance.media_list_new())  # Clear playlist
                    self.list_player.set_playback_mode(0)  # Stop looping
                    # Reset action time to prevent this running every tick
                    self.last_action_time = tick_start

                # Check if a movie is playing
                player_state = self.list_player.get_state()
                logging.debug(f'Media Player state: {player_state}')
                playing = set([1, 2, 3, 4])  # Playing states
                # If no movie if playing, play default movie
                if player_state not in playing:
                    # Wait for movie to not be playing for 0.1s before playing default movie
                    time.sleep(0.1)
                    if self.list_player.get_state() in playing:
                        continue

                    logging.info(f'Playing default movie')
                    media_list = self.create_media_list(self.default_dir)
                    self.play_media_list(media_list, loop=True)

                # Check for RFID input
                try:
                    scanned, folder = rfidpeasyplayer.scan_card(self.reader)
                    logging.debug(f'Raw folder name: "{folder}"')
                    if scanned:
                        folder = folder.strip()
                        logging.info(f'RFID scanned for folder "{folder}"')
                        self.input_delay(self.play_movies_from_folder, 'rfid', 1, folder, loop=True, shuffle=True)
                except TypeError:
                    logging.warning('Error scanning RFID card. Please try again.')

                # Sleep for remainder of tick
                tick_elapsed = time.time() - tick_start
                if tick_elapsed < (1/self.tick_rate):
                    time.sleep((1/self.tick_rate) - tick_elapsed)

        except KeyboardInterrupt:
            logging.info('Closing application...')
            self.reader.cleanup()
            logging.debug([button.close() for k, button in self.buttons.items()])
            sys.exit()

    def create_media_list(self, path, shuffle=False):
        '''
        Create a playlist of all movies in a directory
        *** PARAMS ***
        path: str => Root path to movie folder
        shuffle: bool => Shuffle playlist
        '''
        media_list = self.instance.media_list_new()
        try:
            # Get movies
            logging.debug(f'Files in dir: {os.listdir(path)}')
            movies = [os.path.join(path, m) for m in filter(
                lambda x: self.is_movie_format(x), os.listdir(path))]
            logging.info(f'{len(movies)} movies found in directory.')
        except FileNotFoundError as e:
            logging.warning(e)
            return media_list

        if shuffle:
            random.shuffle(movies)

        for movie in movies:
            media_list.add_media(self.instance.media_new(movie))
        logging.debug(f'Movies:\n{movies}')
        return media_list

    def play_media_list(self, media_list, loop=False):
        '''
        Play a playlist in VLC player
        *** PARAMS ***
        media_list: vlc.Instance.MediaList => Playlist of movies to play
        loop: bool => loop playlist
        '''
        self.last_action_time = time.time()  # Set last input for action timeout

        self.list_player.set_media_list(media_list)
        logging.info(f'Playing media list of length {len(media_list)}')

        if loop:
            self.list_player.set_playback_mode(1)
            logging.debug('Looping set to True')
        else:
            self.list_player.set_playback_mode(0)
            logging.debug('Looping set to False')
        # Skips current movie to start playing the new media list
        self.list_player.next()
        time.sleep(1)
        return self.list_player.play()

    def play_movies_from_folder(self, folder, *args, **kwargs):
        '''
        Create and play a playlist of all movies in a movie folder
        '''
        folder = folder.strip()
        folder_path = os.path.join(self.movies_dir, folder)
        if 'shuffle' in kwargs:
            media_list = self.create_media_list(folder_path, shuffle=kwargs.get('shuffle'))
        else:
            media_list = self.create_media_list(folder_path)

        if 'loop' in kwargs:
            return self.play_media_list(media_list, loop=kwargs.get('loop'))

        logging.info(f'Playing movies from folder "{folder}"')
        return self.play_media_list(media_list)

    def fast_forward(self, seconds):
        ms = seconds * 1000
        played_time = self.list_player_media_player.get_time()
        logging.info(f'Forwarding {seconds} seconds..')
        self.list_player_media_player.set_time(played_time + ms)

    def rewind(self, seconds):
        ms = seconds * 1000
        played_time = self.list_player_media_player.get_time()
        logging.info(f'Rewinding {seconds} seconds..')
        self.list_player_media_player.set_time(played_time - ms)

    def is_movie_format(self, file):
        '''
        Check if a file is an accepted movie format
        '''
        for _format in self.movie_formats:
            if _format in file:
                return True
        return False

    def set_input_listeners(self):
        '''
        Set listeners for the input buttons
        '''
        # Time delay between input groups, in seconds
        movie_delay = 3
        control_delay = 1

        self.buttons = defaultdict()
        # Movie Buttons #
        self.buttons['b1'] = Button('BOARD10')
        self.buttons['b1'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b1', loop=True, shuffle=True)
        self.buttons['b2'] = Button('BOARD13')
        self.buttons['b2'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b2', loop=True, shuffle=True)
        self.buttons['b3'] = Button('BOARD29')
        self.buttons['b3'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b3', loop=True, shuffle=True)
        self.buttons['b4'] = Button('BOARD32')
        self.buttons['b4'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b4', loop=True, shuffle=True)
        self.buttons['b5'] = Button('BOARD33')
        self.buttons['b5'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b5', loop=True, shuffle=True)
        self.buttons['b6'] = Button('BOARD35')
        self.buttons['b6'].when_pressed = lambda: self.input_delay(self.play_movies_from_folder, 'movie', movie_delay, 'b6', loop=True, shuffle=True)
        # Controls #
        self.buttons['fast_forward'] = Button('BOARD3')
        self.buttons['fast_forward'].when_pressed = lambda: self.input_delay(self.fast_forward, 'control', control_delay, 30)
        self.buttons['rewind'] = Button('BOARD7')
        self.buttons['rewind'].when_pressed = lambda: self.input_delay(self.rewind, 'control', control_delay, 30)
        # Play/Pause. Hold for 3 seconds to stop player
        self.buttons['pause'] = Button('BOARD16', hold_time=3)
        self.buttons['pause'].when_held = lambda: self.play_pause_held(self.input_delay, self.list_player.stop, 'control', control_delay)
        self.buttons['pause'].when_released = lambda: self.play_pause_released(self.input_delay, self.list_player.pause, 'control', control_delay)

    def input_delay(self, callback, input_id, delay, *args, **kwargs):
        '''
        Control the delay between different input types
        input_id: str => the input type
        delay: str/float => input delay, in seconds
        func: function => function to call if delay has finished
        *args => arguments to pass to func
        '''
        logging.debug(f'"{input_id}" was input')
        if self.last_input_times[input_id] + delay < time.time():
            self.last_input_times[input_id] = time.time()
            return callback(*args, **kwargs)

    def play_pause_held(self, func, *args, **kwargs):
        self.play_pause_was_held = True
        return func(*args, **kwargs)

    def play_pause_released(self, func, *args, **kwargs):
        # Ignore input if button was held
        if self.play_pause_was_held:
            self.play_pause_was_held = False
            return
        return func(*args, **kwargs)


if __name__ == "__main__":
    player = PeasyPlayer()
    player.start()
