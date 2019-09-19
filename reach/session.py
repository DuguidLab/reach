"""
Sessions
========

:class:`.Session` objects interface with a raspberry pi to sequence training
sessions and record data.

"""
# pylint: disable=unused-argument


import json
import operator
import random
import signal
import textwrap
import time

from reach.raspberry import _RPi
from reach.utilities import enforce_suffix, lazy_property


class Session:
    """
    Controls a single training session and its behavioural data.

    Attributes
    ----------
    data : :class:`dict`
        Stores all training data that is saved and loaded from the training
        JSONs. Can be passed as a kwarg to pre-fill entries.

    """

    def __init__(self, data=None):
        """
        Instantiate a representation of a training session.
        """

        self.data = {}
        if data is not None:
            self.data.update(data)

        # These attributes track state during training
        self._outcome = 0
        self._iti_broken = False
        self._current_spout = None
        self._water_at_cue_onset = None
        self._rpi = None
        self._reward_count = 0

    @classmethod
    def init_all_from_file(cls, json_path=None):
        """
        Generate a :class:`list` of :class:`Session` objects from data stored
        in a Training JSON.

        Parameters
        ----------
        json_path : :class:`str`
            Training JSON to read data from.

        """
        json_path = enforce_suffix(json_path, '.json')

        with open(json_path, 'r') as json_file:
            file_data = json.load(json_file)

        training_data = [
            cls(data=session_data) for session_data in file_data
        ]

        return training_data

    def run(self, config):
        """
        Begin a training session.

        Parameters
        ----------
        config : :class:`dict`
            Training settings.

        """

        random.seed()

        data = self.data
        data.update(config)

        if 'resets_timepoints' in data:
            print('resets_timepoints key found in session data.')
            SystemError('Cancelling.')

        self.spont_reach_spouts = []
        data['spont_reach_timepoints'] = []
        data['resets_timepoints'] = [[], []]

        self._water_at_cue_onset = data['shaping']

        self._display_training_settings()

        self._rpi = _RPi(data['spout_count'])
        self._rpi.wait_to_start()

        signal.signal(
            signal.SIGINT,
            self._end_session_manually
        )

        now = time.time()
        data['start_time'] = now
        data['end_time'] = now + data['duration']

        trial_count = 0

        while now < data['end_time']:
            trial_count += 1
            self._outcome = 0

            print("_________________________________________")
            print("# ---- Starting trial #%i -- %4.0f s ---- #"
                  % (trial_count, now - data['start_time']))

            self._current_spout = random.randint(0, data['spout_count'] - 1)
            self._inter_trial_interval()
            self._trial()

            print(f"Total rewards: {self._reward_count}")
            now = time.time()

        self._end_session()

    def _display_training_settings(self):
        """
        Display the training settings that will be used for the upcoming
        training session.
        """
        data = self.data
        iti_min, iti_max = data['iti']

        print(textwrap.dedent(
            f"""
            _________________________________

            Spouts:      {data['spout_count']}
            Duration:    {data['duration']} s
            Cue:         {data['cue_duration_ms']} ms
            ITI:         {iti_min} - {iti_max} ms
            Shaping:     {data['shaping']}
            _________________________________
            """
        ))

    def _inter_trial_interval(self):
        """
        Run inter-trial interval during training session.

        During the inter-trial interval we start listening for mouse movements
        using the touch sensors. Shaping can be toggled by pressing the start
        button.

        """
        self._rpi.monitor_sensors(
            self._reset_iti_callback,
            self._increase_spont_reaches_callback
        )

        self._rpi.set_button_callback(0, self._reverse_shaping_callback)
        self._water_at_cue_onset = self.data['shaping']

        while True:
            self._rpi.wait_for_rest()
            self._iti_broken = False

            now = time.time()
            iti_duration = random.uniform(*self.data['iti']) / 1000
            trial_end = now + iti_duration
            print(f"Counting down {iti_duration:.2f}s")

            while now < trial_end and not self._iti_broken:
                time.sleep(0.020)
                now = time.time()

            if self._iti_broken:
                continue
            else:
                break

        self._rpi.disable_sensors()

    def _reset_iti_callback(self, pin):
        """
        Callback function executed when the inter-trial interval is broken when
        the mouse prematurely lifts either paw from the paw rests.

        Parameters
        ----------
        pin : int
            Pin number listening to the touch sensor that detected the
            movement.

        """
        self._iti_broken = True
        self.data['resets_timepoints'][
            self._rpi.paw_pins.index(pin)
        ].append(time.time())

    def _increase_spont_reaches_callback(self, pin):
        """
        Callback function executed when a spontaneous reach is made during the
        inter-trial interval.

        Parameters
        ----------
        pin : int
            Pin number listening to the touch sensor that detected the
            spontaneous reach.

        """
        self.spont_reach_spouts.append(pin)
        self.data['spont_reach_timepoints'].append(time.time())

    def _reverse_shaping_callback(self, pin):
        """
        Callback function applied to start button that reverses the state of
        the shaping boolean i.e. switches water dispensing between cue onset
        and grasp for the next trial.

        Parameters
        ----------
        pin : int
            Passed to function by RPi.GPIO event callback; ignored.

        """
        self._water_at_cue_onset = not self._water_at_cue_onset

    def _trial(self):
        """
        Run trial during training session.
        """
        current_spout = self._current_spout
        self._rpi.start_trial(
            current_spout,
            self._reward_callback,
            self._incorrect_grasp_callback
        )

        if self._water_at_cue_onset:
            self._rpi.dispense_water(
                current_spout,
                self.data['reward_duration_ms']
            )

        now = time.time()
        cue_end = now + self.data['cue_duration_ms'] / 1000

        while not self._outcome and now < cue_end:
            time.sleep(0.008)
            now = time.time()

        self._rpi.end_trial()

        if self._outcome == 1:
            print("Successful reach!")
            self._reward_count += 1
            time.sleep(self.data['reward_duration_ms'] / 1000)

        elif self._outcome == 2:
            print("Incorrect reach!")
            time.sleep(self.data['reward_duration_ms'] / 1000)

        else:
            print("Missed reach")

    def _reward_callback(self, pin):
        """
        Callback function executed upon successful grasp of illuminated reach
        target during trial.

        Parameters
        ----------
        pin : int
            Passed to function by RPi.GPIO event callback; ignored.

        """
        self._rpi.successful_grasp(self._current_spout)
        self._outcome = 1
        if not self._water_at_cue_onset:
            self._rpi.dispense_water(
                self._current_spout,
                self.data['reward_duration_ms']
            )

    def _incorrect_grasp_callback(self, pin):
        """
        Callback function executed upon grasp of incorrect reach target during
        trial.

        Parameters
        ----------
        pin : int
            Passed to function by RPi.GPIO event callback; ignored.

        """
        self._rpi.incorrect_grasp(self._current_spout)
        self._outcome = 2

    def _end_session_manually(self, signal_number=None, frame=None):
        """
        Control-C signal handler used during live training sessions that allows
        for clean exiting and saving of collected data.

        Parameters
        ----------
        signal_number : int, optional
            Passed to function by signal.signal; ignored.

        frame : int, optional
            Passed to function by signal.signal; ignored.

        """
        print("\nExiting.")
        self._end_session(manual=True)

    def _end_session(self, manual=False):
        """
        End the current training session: uninitialise the raspberry pi GPIO
        pins, reorganise collected data, and display final training results.

        Parameters
        ----------
        manual : bool, optional
            Specifies whether this function call was the result of a Ctrl-C
            press interrupting a training session.

        """
        self._rpi.cleanup()
        self._collate_data(manual=manual)
        self._display_training_results()
        print('\a')

    def _collate_data(self, manual=False):
        """
        Reorganise collected training data into the final form saved in
        training JSONs.

        Parameters
        ----------
        manual : bool, optional
            Specifies whether the session was ended by a Ctrl-C press
            interrupting the training session.

        """
        data = self.data

        if manual:
            data['end_time'] = time.time()
            data['duration'] = data['end_time'] - data['start_time']

        data['cue_timepoints'] = []
        data['touch_timepoints'] = []
        spont_reach_timepoints = [[], []]

        for idx, spout in enumerate(self._rpi.spouts):
            # pylint: disable=cell-var-from-loop
            self.spont_reach_spouts = list(map(
                lambda x: idx if x == spout['touch'] else x,
                self.spont_reach_spouts
            ))

            spont_reach_timepoints[idx].extend([
                b for a, b in zip(
                    self.spont_reach_spouts,
                    data['spont_reach_timepoints']
                ) if idx
            ])

            data['cue_timepoints'].append(spout['cue_timepoints'])
            data['touch_timepoints'].append(spout['touch_timepoints'])

        data['spont_reach_timepoints'] = spont_reach_timepoints

        data['cued_lift_timepoints'] = self._rpi.lift_timepoints

        data['date'] = time.strftime('%Y-%m-%d')
        data['start_time'] = time.strftime(
            '%H:%M:%S', time.localtime(data['start_time'])
        )
        data['end_time'] = time.strftime(
            '%H:%M:%S', time.localtime(data['end_time'])
        )

    def _display_training_results(self):
        """
        Print training results at the end of the session.
        """
        data = self.data
        trial_count = len(data['cue_timepoints'])
        miss_count = trial_count - self._reward_count
        reward_perc = 100 * self._reward_count / trial_count
        miss_perc = 100 * miss_count / trial_count
        iti_resets = (
            len(data['resets_timepoints'][0]),
            len(data['resets_timepoints'][1]),
        )

        print(textwrap.dedent(f"""
        _________________________________
        # __________ The End __________ #

        Trials:            {trial_count}
        Correct reaches:   {self._reward_count} ({reward_perc:0.1f}%)
        Missed cues:       {miss_count} ({miss_perc:0.1f}%)
        Spont. reaches:    {len(self.spont_reach_spouts)}
        ITI resets:        {sum(iti_resets)}
            left paw:      {iti_resets[0]}
            right paw:     {iti_resets[1]}
        # _____________________________ #

        1000 uL - {self._reward_count} * 6 uL
                    = {1000 - self._reward_count * 6} uL
        """))

    def _prompt_to_add_training_notes(self):
        """
        Prompt the user at the end of the training session (and after
        displaying training results) to ask if they want to add notes to the
        data.
        """
        self.data["notes"] = input("\nAdd any notes to save:\n")

    @lazy_property
    def reaction_times(self):
        """
        List of reaction times for this training session.

        Returns
        -------
        :class:`list` of :class:`float`\s
            Chronological list of reaction times in milliseconds.

        """

        touch_timepoints = []
        for sublist in self.data['touch_timepoints']:
            touch_timepoints.extend(sublist)

        cue_timepoints = []
        for sublist in self.data['cue_timepoints']:
            cue_timepoints.extend(sublist)

        return list(map(operator.sub, touch_timepoints, cue_timepoints))
