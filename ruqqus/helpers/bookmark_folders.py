"""Bookmark folders: the rules, without a database (the routes are in routes/bookmark_folders.py).

A member sorts what they bookmarked into folders of their own. A bookmark is in one folder or none.
The History page's Bookmarked tab is filtered with `?folder=`: absent = everything, `none` = the
unsorted bookmarks, or a folder's id."""
import re

MAX_FOLDERS = 30
NAME_CHARS = 40

# words the address already means something by, so a folder can't be called that
RESERVED = ("all", "none")

ALL, UNSORTED = "all", "none"


class FolderError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def clean_name(raw):
    """The folder name as it is kept: one line, single spaces, 1 to NAME_CHARS characters."""
    name = re.sub(r"\s+", " ", (raw or "").replace("\x00", " ")).strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise FolderError("A folder name can't hold control characters.")
    if not name:
        raise FolderError("Give the folder a name.")
    if len(name) > NAME_CHARS:
        raise FolderError(f"A folder name can be {NAME_CHARS} characters at most.")
    if name.lower() in RESERVED:
        raise FolderError(f"“{name}” is already used for a view of your bookmarks. Pick another name.")
    return name


def check_new(name, existing_names, count):
    """The reason a folder called `name` can't be made next to `existing_names` (all the member's
    folder names), or None. `name` is already clean."""
    taken = {other.lower() for other in existing_names}
    if name.lower() in taken:
        return f"You already have a folder called {name}."
    if count >= MAX_FOLDERS:
        return f"You can have {MAX_FOLDERS} folders at most."
    return None


def check_rename(name, existing_names_without_this_one):
    taken = {other.lower() for other in existing_names_without_this_one}
    if name.lower() in taken:
        return f"You already have a folder called {name}."
    return None


def parse_id(raw):
    """A folder id from text: ASCII digits only (str.isdigit() also takes other scripts' digits),
    above zero and inside a database integer. None for anything else."""
    value = (raw or "").strip()
    if re.fullmatch(r"[0-9]{1,10}", value) and 0 < int(value) < 2 ** 31:
        return int(value)
    return None


def parse_filter(raw):
    """The `?folder=` of the Bookmarked tab: (ALL, None), (UNSORTED, None) or ("folder", id)."""
    value = (raw or "").strip().lower()
    if not value or value == ALL:
        return ALL, None
    if value == UNSORTED:
        return UNSORTED, None
    folder_id = parse_id(value)
    return ("folder", folder_id) if folder_id else (ALL, None)


def query_value(kind, folder_id):
    """The `folder=` text for a link to a view of the bookmarks (empty for all of them)."""
    if kind == UNSORTED:
        return UNSORTED
    if kind == "folder":
        return str(folder_id)
    return ""
