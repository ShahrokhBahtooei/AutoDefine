# AutoDefine Anki Add-on
# Auto-defines words, optionally adding pronunciation and images.
#
# Copyright (c) 2014 - 2019 Robert Sanek    robertsanek.com    rsanek@gmail.com
# https://github.com/z1lc/AutoDefine                      Licensed under GPL v2

import os
from collections import namedtuple

import platform
import re
import traceback
import urllib.error
import urllib.parse
import urllib.request
from anki import version
from anki.hooks import addHook
from aqt import mw
from aqt.utils import showInfo, tooltip
from http.client import RemoteDisconnected
from urllib.error import URLError
from xml.etree import ElementTree as ET

from .libs import webbrowser

# --------------------------------- SETTINGS ---------------------------------

# Get your unique API key by signing up at http://www.dictionaryapi.com/
MERRIAM_WEBSTER_API_KEY = "YOUR_KEY_HERE"

# Index of field to insert definitions into (use -1 to turn off)
DEFINITION_FIELD = 1

# Ignore archaic/obsolete definitions?
IGNORE_ARCHAIC = True

# Get your unique API key by signing up at http://www.dictionaryapi.com/
MERRIAM_WEBSTER_MEDICAL_API_KEY = "YOUR_KEY_HERE"

# Open a browser tab with an image search for the same word?
OPEN_IMAGES_IN_BROWSER = False

# Which dictionary should AutoDefine prefer to get definitions from? Available options are COLLEGIATE and MEDICAL.
PREFERRED_DICTIONARY = "COLLEGIATE"

# Index of field to insert pronunciations into (use -1 to turn off)
PRONUNCIATION_FIELD = 0

# Index of field to insert phonetic transcription into (use -1 to turn off)
PHONETIC_TRANSCRIPTION_FIELD = -1

# Index of field to insert pronunciations into (use -1 to turn off)
DEDICATED_INDIVIDUAL_BUTTONS = False

PRIMARY_SHORTCUT = "ctrl+alt+e"

DEFINE_ONLY_SHORTCUT = ""

PRONOUNCE_ONLY_SHORTCUT = ""

PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT = ""

# Collegiate Dictionary API XML documentation: http://goo.gl/LuD83A
# Medical Dictionary API XML documentation: https://goo.gl/akvkbB
#
# http://www.dictionaryapi.com/api/v1/references/collegiate/xml/WORD?key=KEY
# https://www.dictionaryapi.com/api/references/medical/v2/xml/WORD?key=KEY
#
# Rough XML Structure:
# <entry_list>
#   <entry id="word[1]">
#     <sound>
#       <wav>soundfile.wav</wav>
#     </sound>
#     <fl>verb</fl>
#     <def>
#       <sensb>  (medical API only)
#         <sens>  (medical API only)
#           <dt>:actual definition</dt>
#           <ssl>obsolete</ssl> (refers to next <dt>)
#           <dt>:another definition</dt>
#         </sens>  (medical API only)
#       </sensb>  (medical API only)
#     </def>
#   </entry>
#   <entry id="word[2]">
#     ... (same structure as above)
#   </entry>
# </entry_list>


def get_definition(editor,
                   force_pronounce=False,
                   force_definition=False,
                   force_phonetic_transcription=False):

    editor.saveNow(lambda: _get_definition(editor,
                                           force_pronounce,
                                           force_definition,
                                           force_phonetic_transcription))


def get_definition_force_pronunciation(editor):
    get_definition(editor, force_pronounce=True)


def get_definition_force_definition(editor):
    get_definition(editor, force_definition=True)


def get_definition_force_phonetic_transcription(editor):
    get_definition(editor, force_phonetic_transcription=True)


def validate_settings():
    # ideally, we wouldn't have to force people to individually register, but the API limit is just 1000 calls/day.

    if PREFERRED_DICTIONARY not in ("COLLEGIATE", "MEDICAL"):
        message = "Setting PREFERRED_DICTIONARY must be set to either COLLEGIATE or MEDICAL. Current setting: '%s'" \
                  % PREFERRED_DICTIONARY
        showInfo(message)
        return

    if PREFERRED_DICTIONARY == "MEDICAL" and MERRIAM_WEBSTER_MEDICAL_API_KEY == "YOUR_KEY_HERE":
        message = "The preferred dictionary was set to MEDICAL, but no API key was provided.\n" \
                  "Please register for one at www.dictionaryapi.com."
        showInfo(message)
        webbrowser.open("https://www.dictionaryapi.com/", 0, False)
        return

    if MERRIAM_WEBSTER_API_KEY == "YOUR_KEY_HERE":
        message = "AutoDefine requires use of Merriam-Webster's Collegiate Dictionary with Audio API. " \
                  "To get functionality working:\n" \
                  "1. Go to www.dictionaryapi.com and sign up for an account, requesting access to " \
                  "the Collegiate dictionary. You may also register for the Medical dictionary.\n" \
                  "2. In Anki, go to Tools > Add-Ons. Select AutoDefine, click \"Config\" on the right-hand side " \
                  "and replace YOUR_KEY_HERE with your unique API key.\n"
        showInfo(message)
        webbrowser.open("https://www.dictionaryapi.com/", 0, False)
        return


ValidAndPotentialEntries = namedtuple('Entries', ['valid', 'potential'])


def _focus_zero_field(editor):
    # no idea why, but sometimes web seems to be unavailable
    if editor.web:
        editor.web.eval("focusField(%d);" % 0)


def _get_preferred_valid_and_potential_entries(word):
    potential_entries = []
    entries = None
    for dic_entries in _obtain_related_entries_from_first_unchecked_dic(word):
        entries = filter_entries_lower_and_potential(word, dic_entries)
        potential_entries.extend(entries.potential)
        if entries.valid:
            break

    return entries.valid, potential_entries


def _obtain_related_entries_from_first_unchecked_dic(word):
    collegiate_url = "http://www.dictionaryapi.com/api/v1/references/collegiate/xml/" + \
                     urllib.parse.quote_plus(word) + "?key=" + MERRIAM_WEBSTER_API_KEY
    medical_url = "https://www.dictionaryapi.com/api/references/medical/v2/xml/" + \
                  urllib.parse.quote_plus(word) + "?key=" + MERRIAM_WEBSTER_MEDICAL_API_KEY

    urls = [collegiate_url, medical_url]

    if PREFERRED_DICTIONARY != "COLLEGIATE":
        urls.reverse()

    for url in urls:
        yield get_entries_from_api(word, url)


def filter_entries_lower_and_potential(word, all_entries):
    valid_entries = extract_valid_entries(word, all_entries)
    maybe_entries = []
    if not valid_entries:
        valid_entries = extract_valid_entries(word, all_entries, True)
        if not valid_entries:
            for entry in all_entries:
                maybe_entries.append(entry)

    return ValidAndPotentialEntries(valid_entries, maybe_entries)


def extract_valid_entries(word, all_entries, lower=False):
    valid_entries = []
    for entry in all_entries:
        if lower:
            if entry.attrib["id"][:len(word) + 1].lower() == word.lower() + "[" \
                    or entry.attrib["id"].lower() == word.lower():
                valid_entries.append(entry)
        else:
            if entry.attrib["id"][:len(word) + 1] == word + "[" \
                    or entry.attrib["id"] == word:
                valid_entries.append(entry)

    return valid_entries


def get_entries_from_api(word, url):
    if "YOUR_KEY_HERE" in url:
        return []

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:62.0)'
                                                                 ' Gecko/20100101 Firefox/62.0'})
        returned = urllib.request.urlopen(req).read()
        if "Invalid API key" in returned.decode("UTF-8"):
            showInfo("API key '%s' is invalid. Please double-check you are using the key labeled \"Key (Dictionary)\". "
                     "A web browser with the web page that lists your keys will open." % url.split("?key=")[1])
            webbrowser.open("https://www.dictionaryapi.com/account/my-keys.htm")
            return []

        if "Results not found" in returned.decode("UTF-8"):
            return []

        etree = ET.fromstring(returned)
        return etree.findall("entry")

    except URLError:
        return []

    except (ET.ParseError, RemoteDisconnected):
        showInfo("Couldn't parse API response for word '%s'. "
                 "Please submit an issue to the AutoDefine GitHub (a web browser window will open)." % word)
        webbrowser.open("https://github.com/z1lc/AutoDefine/issues/new?title=Parse error for word '%s'"
                        "&body=Anki Version: %s%%0APlatform: %s %s%%0AURL: %s%%0AStack Trace: %s"
                        % (word, version, platform.system(), platform.release(), url, traceback.format_exc()), 0, False)


def _get_word(editor):
    word = ""
    maybe_web = editor.web
    if maybe_web:
        word = maybe_web.selectedText()

    if word is None or word == "":
        maybe_note = editor.note
        if maybe_note:
            word = maybe_note.fields[0]

    word = clean_html(word).strip()
    return word


def _get_definition(editor,
                    force_pronounce=False,
                    force_definition=False,
                    force_phonetic_transcription=False):

    cp = CommandProvider(editor)
    cp.run_commands(force_definition, force_phonetic_transcription, force_pronounce)


class CommandProvider:
    def __init__(self, editor):
        self.editor = editor
        self._word = _get_word(editor)
        self._valid_entries = []
        self._insert_queue = InsertQueue()
        self._valid_undefined_entries = []
        self._info_not_found = []

    def run_commands(self, force_definition, force_phonetic_transcription, force_pronounce):
        validate_settings()  # TODO Check for continuing do()

        if self._word == "":
            tooltip("AutoDefine: No text found in note fields.")
            return

        self._valid_entries, potential_entries = _get_preferred_valid_and_potential_entries(self._word)

        if not self._valid_entries:
            self._derive_valid_undefined_entries_if_exist(potential_entries)

            if not self._valid_undefined_entries:
                self._announce_no_entry_and_suggest_potentials(potential_entries)

                _focus_zero_field(self.editor)
                return

        for command in self._determine_commands(force_definition,
                                                force_phonetic_transcription,
                                                force_pronounce):
            command()

        self._insert_queue.transfer_to_fields(self.editor)

        self._announce_unavailable_info_if_exists()

        self._search_google_images_for_the_word_via_os_browser_if_needed()

        _focus_zero_field(self.editor)

    def _derive_valid_undefined_entries_if_exist(self, potential_entries):
        for potential in potential_entries:
            for derivative_element in potential.findall("uro"):
                spelling_element = derivative_element.find("ure")
                if spelling_element.text.replace("*", "").casefold() == self._word.casefold():
                    root = potential.attrib["id"]
                    self._save_root_in_entry(derivative_element, root)
                    self._valid_undefined_entries.append(derivative_element)

    @staticmethod
    def _save_root_in_entry(entry, root):
        entry.tail = root

    def _announce_no_entry_and_suggest_potentials(self, potential_entries):
        potentials = list(dict.fromkeys(
            self._remove_indexing_number_at_the_end(potential.attrib["id"])
            for potential in potential_entries
        ))

        msg = f"No entry found in Merriam-Webster dictionary for word '{self._word}'."
        if potentials:
            msg += f" Potential matches: <b>{'</b>, <b>'.join(potentials)}</b>"

        tooltip(msg)

    @staticmethod
    def _remove_indexing_number_at_the_end(word):
        # re: Remove numbers formatted like [2], [13], etc., from the end of the suggested word
        return re.sub(r'\[\d+\]$', "", word)

    def _determine_commands(self, force_definition, force_phonetic_transcription, force_pronounce):
        if force_pronounce:
            yield self._add_vocal_pronunciation

        elif force_phonetic_transcription:
            yield self._add_phonetic_transcription

        elif force_definition:
            yield self._add_definition

        else:
            if PRONUNCIATION_FIELD > -1:
                yield self._add_vocal_pronunciation

            if PHONETIC_TRANSCRIPTION_FIELD > -1:
                yield self._add_phonetic_transcription

            if DEFINITION_FIELD > -1:
                yield self._add_definition

    def _add_vocal_pronunciation(self):
        # Parse all unique pronunciations, and convert them to URLs as per http://goo.gl/nL0vte
        all_sounds = []
        for entry in self._valid_entries + self._valid_undefined_entries:
            for wav in entry.findall("sound/wav"):
                all_sounds.append(self._download_sound(wav.text))

        if all_sounds:
            to_print = self._prepare_sound_names_to_print(all_sounds)
            self._insert_queue.add(to_print, self._get_final_sound_index())
        else:
            self._info_not_found.append("pronunciation")

    def _download_sound(self, raw_wav):
        # API-specific URL conversions
        if raw_wav[:3] == "bix":
            mid_url = "bix"

        elif raw_wav[:2] == "gg":
            mid_url = "gg"

        elif raw_wav[:1].isdigit():
            mid_url = "number"

        else:
            mid_url = raw_wav[:1]

        wav_url = "http://media.merriam-webster.com/soundc11/" + mid_url + "/" + raw_wav
        return self.editor.urlToFile(wav_url).strip()

    @staticmethod
    def _prepare_sound_names_to_print(all_sounds):
        # We want to make this a non-duplicate list so that we only get unique sound files.
        all_sounds = list(dict.fromkeys(all_sounds))
        to_print = ""
        for sound_local_filename in all_sounds:
            to_print += f"[sound:{sound_local_filename}]"

        return to_print

    def _get_final_sound_index(self):
        final_pronounce_index = PRONUNCIATION_FIELD
        fields = mw.col.models.fieldNames(self.editor.note.model())
        for field in fields:
            if '🔊' in field:
                final_pronounce_index = fields.index(field)
                break

        return final_pronounce_index

    def _add_phonetic_transcription(self):
        all_transcriptions = []
        for entry in self._valid_entries + self._valid_undefined_entries:
            if entry.find("pr") is not None:
                phonetic_transcription = entry.find("pr").text
                part_of_speech = self._abbreviate_part_of_speech(entry.find("fl").text)

                row = f"<b>{part_of_speech}</b> \\{phonetic_transcription}\\"
                all_transcriptions.append(row)

        if all_transcriptions:
            to_print = "<br>".join(all_transcriptions)
            self._insert_queue.add(to_print, PHONETIC_TRANSCRIPTION_FIELD)
        else:
            self._info_not_found.append("phonetic transcription")  # TODO: Consider good-bye

    part_of_speech_abbreviation = {
        "verb": "v.",
        "noun": "n.",
        "adverb": "adv.",
        "adjective": "adj."}

    @classmethod
    def _abbreviate_part_of_speech(cls, part_of_speech):
        return cls.part_of_speech_abbreviation.get(part_of_speech, part_of_speech)

    def _add_definition(self):
        if self._valid_undefined_entries:
            self._info_not_found.append("definition")
            return

        definition_array = []
        # Extract the type of word this is
        for entry in self._valid_entries:
            this_def = entry.find("def")
            if entry.find("fl") is None:
                continue
            fl = entry.find("fl").text
            fl = self._abbreviate_part_of_speech(fl)

            this_def.tail = "<b>" + fl + "</b>"  # save the functional label (noun/verb/etc) in the tail

            # the <ssl> tag will contain the word 'obsolete' if the term is not in use anymore. However, for some
            # reason, the tag precedes the <dt> that it is associated with instead of being a child. We need to
            # associate it here so that later we can either remove or keep it regardless.
            previous_was_ssl = False
            for child in this_def:
                # this is a kind of poor way of going about things, but the ElementTree API
                # doesn't seem to offer an alternative.
                if child.text == "obsolete" and child.tag == "ssl":
                    previous_was_ssl = True
                if previous_was_ssl and child.tag == "dt":
                    child.tail = "obsolete"
                    previous_was_ssl = False

            definition_array.append(this_def)

        to_return = ""
        for definition in definition_array:
            last_functional_label = ""
            medical_api_def = definition.findall("./sensb/sens/dt")
            # sometimes there's not a definition directly (dt) but just a usage example (un):
            if len(medical_api_def) == 1 and not medical_api_def[0].text:
                medical_api_def = definition.findall("./sensb/sens/dt/un")
            for dtTag in (definition.findall("dt") + medical_api_def):

                if dtTag.tail == "obsolete":
                    dtTag.tail = ""  # take away the tail word so that when printing it does not show up.
                    if IGNORE_ARCHAIC:
                        continue

                # We don't really care for 'verbal illustrations' or 'usage notes',
                # even though they are occasionally useful.
                for usageNote in dtTag.findall("un"):
                    dtTag.remove(usageNote)
                for verbalIllustration in dtTag.findall("vi"):
                    dtTag.remove(verbalIllustration)

                # Directional cross reference doesn't make sense for us
                for dxTag in dtTag.findall("dx"):
                    for dxtTag in dxTag.findall("dxt"):
                        for dxnTag in dxtTag.findall("dxn"):
                            dxtTag.remove(dxnTag)

                # extract raw XML from <dt>...</dt>
                to_print = ET.tostring(dtTag, "", "xml").strip().decode("utf-8")
                # attempt to remove 'synonymous cross reference tag' and replace with semicolon
                to_print = to_print.replace("<sx>", "; ")
                # attempt to remove 'Directional cross reference tag' and replace with semicolon
                to_print = to_print.replace("<dx>", "; ")
                # remove all other XML tags
                to_print = re.sub('<[^>]*>', '', to_print)
                # remove all colons, since they are usually useless and have been replaced with semicolons above
                to_print = re.sub(':', '', to_print)
                # erase space between semicolon and previous word, if exists, and strip any extraneous whitespace
                to_print = to_print.replace(" ; ", "; ").strip()
                to_print += "\n<br>"

                # add verb/noun/adjective
                if last_functional_label != definition.tail:
                    to_print = definition.tail + " " + to_print
                last_functional_label = definition.tail
                to_return += to_print

        # final cleanup of <sx> tag bs
        to_return = to_return.replace(".</b> ; ", ".</b> ")  # <sx> as first definition after "n. " or "v. "
        to_return = to_return.replace("\n; ", "\n")  # <sx> as first definition after newline
        self._insert_queue.add(to_return, DEFINITION_FIELD)

    def _announce_unavailable_info_if_exists(self):
        if not self._info_not_found:
            return

        if len(self._info_not_found) == 1:
            msg = f"No <b>{self._info_not_found[0]}</b>"

        elif len(self._info_not_found) == 2:
            msg = f"Neither of the <b>{'</b> and <b>'.join(self._info_not_found)}</b>"

        else:
            msg = f"""None of the <b>{self._info_not_found[0]}</b>,
            <b>{self._info_not_found[1]}</b>, and <b>{self._info_not_found[2]}</b>"""

        msg += f" found for entry '{self._word}'."

        msg += self._print_roots_if_exist()

        tooltip(msg)

    def _print_roots_if_exist(self):
        if not self._valid_undefined_entries:
            return ""

        roots = []
        for entry in self._valid_undefined_entries:
            root = self._derive_saved_root_in_entry(entry)
            roots.append(self._remove_indexing_number_at_the_end(root))

        roots = list(dict.fromkeys(roots))

        return f" Root word: <b>{'</b>, <b>'.join(roots)}</b>"

    @staticmethod
    def _derive_saved_root_in_entry(entry):
        return entry.tail

    def _search_google_images_for_the_word_via_os_browser_if_needed(self):
        if OPEN_IMAGES_IN_BROWSER:
            webbrowser.open("https://www.google.com/search?q= " + self._word +
                            "&safe=off&tbm=isch&tbs=isz:lt,islt:xga", 0, False)


class InsertQueue:
    def __init__(self):
        self._queue = {}

    def add(self, to_print, field_id):
        if field_id not in self._queue:
            self._queue[field_id] = to_print
        else:
            self._queue[field_id] += "<br>" + to_print

    def transfer_to_fields(self, editor):
        for field_id in self._queue:
            insert_into_field(editor, self._queue[field_id], field_id)


def insert_into_field(editor, text, field_id, overwrite=False):
    if len(editor.note.fields) < field_id:
        tooltip("AutoDefine: Tried to insert '%s' into user-configured field number %d (0-indexed), but note type only "
                "has %d fields. Use a different note type with %d or more fields, or change the index in the "
                "Add-on configuration." % (text, field_id, len(editor.note.fields), field_id + 1), period=10000)
        return

    if overwrite:
        editor.note.fields[field_id] = text
    else:
        editor.note.fields[field_id] += text

    editor.loadNote()


# via https://stackoverflow.com/a/12982689
def clean_html(raw_html):
    return re.sub(re.compile('<.*?>'), '', raw_html)


def setup_buttons(buttons, editor):
    main_button = editor.addButton(icon=os.path.join(os.path.dirname(__file__), "images", "icon16.png"),
                                   cmd="AD",
                                   func=get_definition,
                                   tip="AutoDefine Word (%s)" %
                                       ("no shortcut" if PRIMARY_SHORTCUT == "" else PRIMARY_SHORTCUT),
                                   toggleable=False,
                                   label="",
                                   keys=PRIMARY_SHORTCUT,
                                   disables=False)

    define_button = editor.addButton(icon="",
                                     cmd="D",
                                     func=get_definition_force_definition,
                                     tip="AutoDefine: Definition only (%s)" %
                                         ("no shortcut" if DEFINE_ONLY_SHORTCUT == "" else DEFINE_ONLY_SHORTCUT),
                                     toggleable=False,
                                     label="",
                                     keys=DEFINE_ONLY_SHORTCUT,
                                     disables=False)

    pronounce_button = editor.addButton(icon="",
                                        cmd="P",
                                        func=get_definition_force_pronunciation,
                                        tip="AutoDefine: Pronunciation only (%s)" % ("no shortcut"
                                                                                     if PRONOUNCE_ONLY_SHORTCUT == ""
                                                                                     else PRONOUNCE_ONLY_SHORTCUT),
                                        toggleable=False,
                                        label="",
                                        keys=PRONOUNCE_ONLY_SHORTCUT,
                                        disables=False)

    phonetic_transcription_button = editor.addButton(icon="",
                                                     cmd="ə",
                                                     func=get_definition_force_phonetic_transcription,
                                                     tip="AutoDefine: Phonetic Transcription only (%s)" %
                                                         ("no shortcut"
                                                          if PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT == ""
                                                          else PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT),
                                                     toggleable=False,
                                                     label="",
                                                     keys=PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT,
                                                     disables=False)
    buttons.append(main_button)

    if DEDICATED_INDIVIDUAL_BUTTONS:
        buttons.append(define_button)
        buttons.append(pronounce_button)
        buttons.append(phonetic_transcription_button)

    return buttons


addHook("setupEditorButtons", setup_buttons)

if getattr(mw.addonManager, "getConfig", None):
    config = mw.addonManager.getConfig(__name__)
    if '1 required' in config and 'MERRIAM_WEBSTER_API_KEY' in config['1 required']:
        MERRIAM_WEBSTER_API_KEY = config['1 required']['MERRIAM_WEBSTER_API_KEY']
    else:
        showInfo("AutoDefine: The schema of the configuration has changed in a backwards-incompatible way.\n"
                 "Please remove and re-download the AutoDefine Add-on.")

    if '2 extra' in config:
        extra = config['2 extra']
        if 'DEDICATED_INDIVIDUAL_BUTTONS' in extra:
            DEDICATED_INDIVIDUAL_BUTTONS = extra['DEDICATED_INDIVIDUAL_BUTTONS']
        if 'DEFINITION_FIELD' in extra:
            DEFINITION_FIELD = extra['DEFINITION_FIELD']
        if 'IGNORE_ARCHAIC' in extra:
            IGNORE_ARCHAIC = extra['IGNORE_ARCHAIC']
        if 'MERRIAM_WEBSTER_MEDICAL_API_KEY' in extra:
            MERRIAM_WEBSTER_MEDICAL_API_KEY = extra['MERRIAM_WEBSTER_MEDICAL_API_KEY']
        if 'OPEN_IMAGES_IN_BROWSER' in extra:
            OPEN_IMAGES_IN_BROWSER = extra['OPEN_IMAGES_IN_BROWSER']
        if 'PREFERRED_DICTIONARY' in extra:
            PREFERRED_DICTIONARY = extra['PREFERRED_DICTIONARY']
        if 'PRONUNCIATION_FIELD' in extra:
            PRONUNCIATION_FIELD = extra['PRONUNCIATION_FIELD']
        if 'PHONETIC_TRANSCRIPTION_FIELD' in extra:
            PHONETIC_TRANSCRIPTION_FIELD = extra['PHONETIC_TRANSCRIPTION_FIELD']

    if '3 shortcuts' in config:
        shortcuts = config['3 shortcuts']
        if '1 PRIMARY_SHORTCUT' in shortcuts:
            PRIMARY_SHORTCUT = shortcuts['1 PRIMARY_SHORTCUT']
        if '2 DEFINE_ONLY_SHORTCUT' in shortcuts:
            DEFINE_ONLY_SHORTCUT = shortcuts['2 DEFINE_ONLY_SHORTCUT']
        if '3 PRONOUNCE_ONLY_SHORTCUT' in shortcuts:
            PRONOUNCE_ONLY_SHORTCUT = shortcuts['3 PRONOUNCE_ONLY_SHORTCUT']
        if '4 PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT' in shortcuts:
            PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT = shortcuts['4 PHONETIC_TRANSCRIPTION_ONLY_SHORTCUT']
