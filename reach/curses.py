"""
Curses Interface
================

The RPiCurses object is a child class of reach.RPiReal that mimics a real
raspberry pi, but unlike the reach._RPiMock mock pi, _RPiCurses provides a
curses interface that can be used to perform the task in a terminal. This
allows for testing of new code that depends on interactivity of the task.

This file provides some additional functions to help manage the curses
interface.

"""


import curses
import textwrap
import threading
import time
import random

from reach.raspberry import RPiReal
import reach.drawings as drawings


def _get_str_dims(string):
    """
    Get width and height of a multiline string.
    """
    height = string.count('\n')
    width = 0
    for substr in string.splitlines():
        if len(substr) > width:
            width = len(substr)
    return height, width


def _addstr_multiline(win, y_pos, x_pos, string, attr=None):
    """
    Print multiline string to curses window to specified position.
    """
    if attr is None:
        for y, line in enumerate(string.splitlines()):
            win.addstr(
                y_pos + y,
                x_pos,
                line,
            )

    else:
        for y, line in enumerate(string.splitlines()):
            win.addstr(
                y_pos + y,
                x_pos,
                line,
                attr,
            )


def _initialise_curses():
    """
    Initialise curses settings. Returns the curses screen.
    """
    stdscr = curses.initscr()
    stdscr.keypad(True)

    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_BLUE)

    curses.curs_set(0)
    curses.cbreak()
    curses.noecho()
    curses.mouseinterval(200)

    return stdscr


class RPiCurses(RPiReal):
    """
    A mock instance of a raspberry pi and its GPIO pins. This class is a
    fallback for reach.RPiReal when the RPi.GPIO library cannot be loaded,
    which assumes that we are working on a non-raspberry pi machine. This class
    differs from reach._RPiMock in that this class provides a curses-based
    interface to interact with the mock task in a terminal window.

    This subclass overrides most methods to replace all calls to RPi.GPIO to
    instead keep track of hypothetical pin state changes.

    Attributes
    ----------
    _stdscr : curses.window object
        This is the screen window object returned from curses.initscr().

    _dimensions : Tuple of two ints
        (height, width) of the stdscr window.

    _feed : curses.window object
        This window displays a feed of information during the training session.

    _rig : curses.window object
        This window displays the virtual training rig.

    _monitor : threading.Thread object
        Thread that monitors key presses and executes callback functions to
        emulate touch sensor activity.

    _monitoring : boolean
        True when we are monitoring key presses, falsifed to stop monitoring at
        the end of the inter-trial interval..

    _button_callbacks : list of two functions
        Stores functions that will be executed when the virtual buttons are
        pressed during the inter-trial interval.

    """
    def __init__(self, spout_count):
        """
        Initialise virtual raspberry pi and draw rig on screen.
        """
        super().__init__(spout_count)

        self._stdscr = _initialise_curses()
        self._dimensions = self._stdscr.getmaxyx()

        self._feed = curses.newwin(
            self._dimensions[0] - 18, self._dimensions[1],
            0, 0,
        )
        self._feed.border()
        self._feed_text = [''] * (self._dimensions[1] - 2)

        self._rig, rig_x_pos = self._draw_rig()

        self._target_pos = (
            [7, rig_x_pos + 5],
            [7, rig_x_pos + 12],
        )

        self._water_pos = (
            [8, rig_x_pos + 5],
            [8, rig_x_pos + 12],
        )

        self._draw_mouse()
        self._feed.refresh()
        self._rig.refresh()

        self._button_callbacks = [lambda: None] * 2
        self._monitor = None
        self._monitoring = False

    def _initialise_pins(self):
        """
        Set initial state of mock pins and virtual rig.
        """
        self._pin_states = [0] * 27
        for button in self._button_pins:
            self._pin_states[button] = 1

        for spout in self.spouts:
            spout['cue_timepoints'] = []
            spout['touch_timepoints'] = []

    def _draw_rig(self):
        """
        Draw curses version of training rig.
        """
        rig = curses.newwin(
            17, self._dimensions[1],
            int(self._dimensions[0] - 18), 0,
        )
        rig.border()

        _, rig_width = _get_str_dims(drawings.RIG_TEMPLATE)
        rig_x_pos = int(self._dimensions[1] / 2 - rig_width / 2)
        _addstr_multiline(rig, 2, rig_x_pos, drawings.RIG_TEMPLATE)
        _addstr_multiline(rig, 11, 3, drawings.BUTTONS)

        return rig, rig_x_pos

    def _draw_mouse(self):
        """
        Draw mouse ASCII art in corner of rig window.
        """
        mouse = random.choice(drawings.MICE)
        dims = [i + 2 for i in _get_str_dims(mouse)]
        y_pos = int(17 - dims[0])
        x_pos = self._dimensions[1] - dims[1]

        for y, line in enumerate(mouse.splitlines()):
            self._rig.addstr(
                y_pos + y,
                x_pos,
                line,
            )

    def print_to_feed(self, string):
        """
        Print text to self._feed window, scrolling as needed.
        """
        for line in string.splitlines():
            self._feed_text.extend(
                textwrap.wrap(line, width=self._dimensions[0] - 6) or ['']
            )

        for y, line in enumerate(
                self._feed_text[-(self._dimensions[0] - 22):]
        ):
            self._feed.addstr(y + 2, 3, line)
            self._feed.clrtoeol()

        self._feed.border()
        self._feed.refresh()

    def wait_to_start(self):
        """
        Instead of blocking execution, simply print a message.
        """
        self.print_to_feed("Hit button 1 to begin.")

        while True:
            key = self._feed.getkey()
            if key == '1':
                return

    def monitor_sensors(self, reset_iti, increase_spont_reaches):
        """
        Run the main sensor monitoring function asynchronously.
        """
        self._monitor = threading.Thread(
            target=self._monitor_iti,
            args=(reset_iti, increase_spont_reaches),
        )
        self._monitor.start()

    def _monitor_iti(self, reset_iti, increase_spont_reaches):
        """
        This is function asynchronously monitors the virtual touch sensors (key
        presses of the letters v, n, g & h.

        Parameters
        ----------
        reset_iti : func
            Callback function executed upon lift of either paw.

        increase_spont_reaches : func
            Callback function executed upon contact with any spout touch
            sensors.

        """
        curses.flushinp()
        self._monitoring = True

        while True:
            key = self._feed.getkey()
            if not self._monitoring:
                return
            if key == 'v':
                reset_iti(self.paw_pins[0])
            elif key == 'n':
                reset_iti(self.paw_pins[1])
            elif key == 'g':
                increase_spont_reaches(self.spouts[0]['touch'])
            elif key == 'h':
                increase_spont_reaches(self.spouts[1]['touch'])
            elif key == '1':
                self._button_callbacks[0]()
            elif key == '2':
                self._button_callbacks[1]()

    def set_button_callback(self, button, func):
        """
        Add a callback function to be executed upon virtual button press.

        Parameters
        ----------
        button : int
            The index of the button to assign the function to.

        callback_function : func
            Function to be executed upon button press.

        """
        self._button_callbacks[button] = lambda: func(button)

    def wait_for_rest(self):
        """
        Block execution and wait until both paw sensors are held.
        """
        self.print_to_feed("Waiting for rest... ")
        time.sleep(0.200)

    def disable_sensors(self):
        """
        Pretend to remove event detection from all touch sensors at the end of
        the inter-trial interval.
        """
        self._monitoring = False
        curses.ungetch('x')

    def start_trial(self, spout_number, reward_func, incorrect_func):
        """
        Record the trial start time.

        Illuminate a cue, record the time, and add callback functions to be
        executed upon grasp of target spouts during trial.

        Parameters
        ----------
        spout_number : int
            The spout number corresponding to this trial's reach target.

        reward_func : func
            Callback function to be executed upon successful grasp of target
            spout.

        incorrect_func : func
            Callback function to be executed upon incorrect grasp of non-target
            spout.

        """
        self.spouts[spout_number]['cue_timepoints'].append(time.time())

        _addstr_multiline(
            self._rig,
            *self._target_pos[spout_number],
            drawings.TARGET,
            attr=curses.color_pair(1),
        )
        self._rig.refresh()
        self.print_to_feed("Cue illuminated")

        self._monitor = threading.Thread(
            target=self._monitor_trial,
            args=(spout_number, reward_func, incorrect_func),
        )
        self._monitor.start()

    def _monitor_trial(self, spout_number, reward_func, incorrect_func):
        """
        This is function asynchronously monitors the virtual touch sensors (key
        presses of the letters v, n, g & h.

        Parameters
        ----------
        spout_number : int
            The spout number corresponding to this trial's reach target.

        reward_func : func
            Callback function to be executed upon successful grasp of target
            spout.

        incorrect_func : func
            Callback function to be executed upon incorrect grasp of non-target
            spout.

        """
        if spout_number == 0:
            correct_key = 'g'
            incorrect_key = 'h'
        elif spout_number == 1:
            correct_key = 'h'
            incorrect_key = 'g'

        curses.flushinp()
        self._monitoring = True

        while True:
            key = self._feed.getkey()
            self.print_to_feed(f'TRIAL: Pressed {key}')
            if not self._monitoring:
                self.print_to_feed(f'TRIAL: RETURNING')
                return
            if key == 'v':
                self._record_lift_timepoints(self.paw_pins[0])
            elif key == 'n':
                self._record_lift_timepoints(self.paw_pins[1])
            elif key == correct_key:
                reward_func(spout_number)
                time.sleep(1)
            elif key == incorrect_key:
                incorrect_func(abs(spout_number - 1))
                time.sleep(1)

    def _record_lift_timepoints(self, pin):
        """
        Record timepoint of paw lifts during trials.

        Parameters
        ----------
        pin : int
            Pin number listening to the touch sensor that detected the
            movement.

        """
        self.lift_timepoints[self.paw_pins.index(pin)].append(time.time())

    def successful_grasp(self, spout_number):
        """
        Record the time upon successful cued grasp of hypothetical target
        spout.

        Parameters
        ----------
        spout_number : int
            The spout number corresponding to this trial's reach target.

        """
        self.spouts[spout_number]['touch_timepoints'].append(time.time())

    def incorrect_grasp(self, spout_number):
        """
        Record time upon grasp of fictional incorrect spout during mock trial.

        Parameters
        ----------
        spout_number : int
            The spout number corresponding to this trial's reach target.

        """
        self.spouts[1 - spout_number]['touch_timepoints'].append(time.time())

    def dispense_water(self, spout_number, duration_ms):
        """
        Pretend to dispense water from a specified spout.

        Parameters
        ----------
        spout_number : int
            The spout number that would dispense water if this were real.

        duration_ms : int
            The duration in milliseconds to wait pretending to dispense.

        """
        self._rig.addstr(
            *self._water_pos[spout_number],
            drawings.WATER,
            curses.color_pair(2),
        )
        self._rig.refresh()

    def end_trial(self):
        """
        Pretend to disable target spout LED and remove spout touch sensors
        event callbacks.
        """
        self.disable_sensors()
        for target in self._target_pos:
            _addstr_multiline(
                self._rig,
                *target,
                drawings.TARGET,
                attr=curses.color_pair(0),
            )
        self._rig.refresh()

    def cleanup(self, signal_number=None, frame=None):
        """
        Restore terminal state.
        """
        self._stdscr.keypad(False)
        curses.curs_set(1)
        curses.nocbreak()
        curses.echo()
        curses.endwin()
        curses.nl()
