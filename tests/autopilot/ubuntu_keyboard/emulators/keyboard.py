# -*- Mode: Python; coding: utf-8; indent-tabs-mode: nil; tab-width: 4 -*-
#
# Ubuntu Keyboard Test Suite
# Copyright (C) 2013 Canonical
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from collections import defaultdict, namedtuple
from time import sleep
import logging

from autopilot.input import Pointer, Touch
from autopilot.introspection import get_proxy_object_for_existing_process


logger = logging.getLogger(__name__)


# Definitions of enums used within the cpp source code.
class KeyboardState:
    DEFAULT = 0
    SHIFTED = 1
    SYMBOL_1 = 2
    SYMBOL_2 = 3


class KeyAction:
    INSERT = 0
    SHIFT = 1
    BACKSPACE = 2
    SPACE = 3
    SYM = 6
    RETURN = 7
    SWITCH = 11


class UnsupportedKey(RuntimeError):
    pass


class Keyboard(object):

    KeyPos = namedtuple("KeyPos", ['x', 'y', 'h', 'w'])

    # Note (veebers 19-aug-13): this hardcoded right now, but will be reading
    # data from the keyboard itself in the very near future. Moved '/' to
    # primary symbol, default layout can have a .com instead.
    default_keys = "qwertyuiopasdfghjklzxcvbnm."
    shifted_keys = "QWERTYUIOPASDFGHJKLZXCVBNM."
    primary_symbol = "1234567890*#+-=()!?@~/\\';:,."
    secondary_symbol = u"$%<>[]`^|_{}\"&,.\u20ac\xa3\xa5\u20b9\xa7\xa1\xbf" \
        u"\xab\xbb\u201c\u201d\u201e"

    # The ability to name the non-text keys.
    _action_id_to_text = {
        KeyAction.SHIFT: 'SHIFT',
        KeyAction.BACKSPACE: '\b',
        KeyAction.SPACE: ' ',
        KeyAction.RETURN: '\n'
    }

    def __init__(self, pointer=None):
        try:
            maliit = get_proxy_object_for_existing_process(
                connection_name='org.maliit.server'
            )
        except RuntimeError as e:
            e.message = "Unable to find maliit-server dbus object. Has it" \
                "been started with introspection enabled? Original message: " \
                "{original_msg}".format(original_msg=e.message)
            raise e

        try:
            self.keyboard = maliit.select_single("Keyboard")
        except ValueError as e:
            e.message = "There was more than one Keyboard object found," \
                "aborting. ({original_msg})".format(original_msg=e.message)
        else:
            if self.keyboard is None:
                raise RuntimeError(
                    "Unable to find the Keyboard object within the "
                    "maliit server"
                )

        try:
            self.keypad = maliit.select_single(
                "QQuickItem",
                objectName="keyboardKeypad"
            )
        except ValueError as e:
            e.message = "There was more than one keyboard keypad object " \
                "found, aborting. ({original_msg})".format(
                    original_msg=e.message
                )
        else:
            if self.keyboard is None:
                raise RuntimeError(
                    "Unable to find the keypad object within the "
                    "maliit server"
                )

        # Contains instructions on how to move the keyboard into a specific
        # state/layout so that we can successfully press the required key.
        self._state_lookup_table = self._generate_state_lookup_table()
        # Cache the position of the keys
        self._key_pos_table = defaultdict(dict)

        if pointer is None:
            self.pointer = Pointer(Touch.create())
        else:
            self.pointer = pointer

    def dismiss(self):
        """Swipe the keyboard down to hide it.

        :raises: <something> if the state.wait_for fails meaning that the
         keyboard failed to hide.

        """
        if self.is_available():
            x, y, h, w = self.keyboard.globalRect
            x_pos = int(w / 2)
            # start_y: just inside the keyboard, must be a better way than +1px
            start_y = y + 1
            end_y = y + int(h / 2)
            self.pointer.drag(x_pos, start_y, x_pos, end_y)

            self.keyboard.state.wait_for("HIDDEN")

    def is_available(self):
        """Returns true if the keyboard is shown and ready to use."""
        return (
            self.keyboard.state == "SHOWN"
            and not self.keyboard.hideAnimationFinished
        )

    @property
    def current_state(self):
        return self.keyboard.layoutState

    # Much like is_available, but attempts to wait for the keyboard to be
    # ready.
    def wait_for_keyboard_ready(self, timeout=10):
        """Waits for *timeout* for the keyboard to be ready and returns
        true. Returns False if the keyboard fails to be considered ready within
        the alloted time.

        """
        try:
            self.keyboard.state.wait_for("SHOWN", timeout=timeout)
            self.keyboard.hideAnimationFinished.wait_for(
                False,
                timeout=timeout
            )
        except RuntimeError:
            return False
        else:
            return True

    def get_key_position(self, key):
        """Returns the global rect of the given key.

        It may need to do a lookup to update the table of positions.

        """
        current_state = self.keyboard.layoutState
        if self._key_pos_table.get(current_state) is None:
            self._update_pos_table_for_current_state()

        return self._key_pos_table[current_state][key]

    def _update_pos_table_for_current_state(self):
        all_keys = self.keypad.select_many('QQuickText')
        current_state = self.keyboard.layoutState
        labeled_keys = (KeyAction.INSERT, KeyAction.SWITCH, KeyAction.SYM)
        for key in all_keys:
            with key.no_automatic_refreshing():
                key_pos = Keyboard.KeyPos(*key.globalRect)
                if key.action_type in labeled_keys:
                    self._key_pos_table[current_state][key.text] = key_pos
                else:
                    key_text = Keyboard._action_id_to_text[key.action_type]
                    self._key_pos_table[current_state][key_text] = key_pos

    def press_key(self, key):
        """Tap on the key with the internal pointer

        :params key: String containing the text of the key to tap.
        """
        if not self.is_available():
            raise RuntimeError("Keyboard is not on screen")

        if not self._is_special_key(key):
            required_state_for_key = self._get_keys_required_state(key)
            self._switch_keyboard_to_state(required_state_for_key)

        key_rect = self.get_key_position(key)
        self.pointer.click_object(key_rect)

    def type(self, string, delay=0.1):
        """Type the string *string* with a delay of *delay* between each key
        press

        .. note:: The delay provides a minimum delay, it may take longer
        between each press as the keyboard shifts between states etc.

        Only 'normal' or single characters can be typed this way.

        """
        for char in string:
            self.press_key(char)
            sleep(delay)

    def _get_keys_required_state(self, char):
        """Given a character determine which state the keyboard needs to be in
        so that it is visible and can be clicked.

        """

        if char in Keyboard.default_keys:
            return KeyboardState.DEFAULT
        elif char in Keyboard.shifted_keys:
            return KeyboardState.SHIFTED
        elif char in Keyboard.primary_symbol:
            return KeyboardState.SYMBOL_1
        elif char in Keyboard.secondary_symbol:
            return KeyboardState.SYMBOL_2
        else:
            raise UnsupportedKey(
                "Don't know which state key '%s' requires" % char
            )

    def _switch_keyboard_to_state(self, target_state):
        """Given a target_state, presses the required keys to bring the
        keyboard into the correct state.

        :raises: *RuntimeError* if unable to change the keyboard into the
          expected state.

        """
        current_state = self.keyboard.layoutState

        if target_state == current_state:
            return

        instructions = self._state_lookup_table[target_state].get(
            current_state,
            None
        )
        if instructions is None:
            raise RuntimeError(
                "Don't know how to get to state %d from current state (%d)"
                % (target_state, current_state)
            )

        for step in instructions:
            key, expected_state = step
            self.press_key(key)
            self.keyboard.layoutState.wait_for(expected_state)

    def _is_special_key(self, key):
        return key in ["\n", "\b", " ", "SHIFT", "?123", "ABC", "1/2", "2/2"]

    # Give the state that you want and the current state, get instructions on
    # how to move to that state.
    # lookup_table[REQUESTED_STATE][CURRENT_STATE] -> Instructions(Key to
    # press, Expected state after key press.)
    def _generate_state_lookup_table(self):
        return {
            KeyboardState.DEFAULT: {
                KeyboardState.SHIFTED: [
                    ("SHIFT", KeyboardState.DEFAULT)
                ],
                KeyboardState.SYMBOL_1: [
                    ("ABC", KeyboardState.DEFAULT)
                ],
                KeyboardState.SYMBOL_2: [
                    ("ABC", KeyboardState.DEFAULT)
                ],
            },
            KeyboardState.SHIFTED: {
                KeyboardState.DEFAULT: [
                    ("SHIFT", KeyboardState.SHIFTED)
                ],
                KeyboardState.SYMBOL_1: [
                    ("ABC", KeyboardState.DEFAULT),
                    ("SHIFT", KeyboardState.SHIFTED)
                ],
                KeyboardState.SYMBOL_2: [
                    ("ABC", KeyboardState.DEFAULT),
                    ("SHIFT", KeyboardState.SHIFTED)
                ],
            },
            KeyboardState.SYMBOL_1: {
                KeyboardState.DEFAULT: [
                    ("?123", KeyboardState.SYMBOL_1)
                ],
                KeyboardState.SHIFTED: [
                    ("?123", KeyboardState.SYMBOL_1)
                ],
                KeyboardState.SYMBOL_2: [
                    ("2/2", KeyboardState.SYMBOL_1)
                ],
            },
            KeyboardState.SYMBOL_2: {
                KeyboardState.DEFAULT: [
                    ("?123", KeyboardState.SYMBOL_1),
                    ("1/2", KeyboardState.SYMBOL_2)
                ],
                KeyboardState.SHIFTED: [
                    ("?123", KeyboardState.SYMBOL_1),
                    ("1/2", KeyboardState.SYMBOL_2)
                ],
                KeyboardState.SYMBOL_1: [
                    ("1/2", KeyboardState.SYMBOL_2)
                ],
            },
        }
