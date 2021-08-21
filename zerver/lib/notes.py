import weakref
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, MutableMapping, Optional, TypeVar

from zerver.models import Message

_KeyT = TypeVar("_KeyT")
_DataT = TypeVar("_DataT")


class BaseNotes(Generic[_KeyT, _DataT], metaclass=ABCMeta):
    """This class is an abstraction of the "notes" model we use to patch
    extra attributes to an object without modifying the original class
    definition. It maps the object to another object dedicated for the patched
    attributes.

    It is useful in the following cases:

    1. Monkey-patching a class that we don't have control of (e.g.: HttpRequest)
    2. Temporarily adding **type-safe** attributes to an object

    Note that we use a WeakKeyDictionary to ensure that the patched attributes
    won't cause memory leakage when there is no other reference to the patched
    object, but still we need to take care not making any of the attributes of
    _NoteT lead to cyclic reference with itself.
    """

    __notes_map: ClassVar[MutableMapping[Any, Any]] = weakref.WeakKeyDictionary()

    @classmethod
    def get_notes(cls, key: _KeyT) -> _DataT:
        try:
            return cls.__notes_map[key]
        except KeyError:
            cls.__notes_map[key] = cls.init_notes()
            return cls.__notes_map[key]

    @classmethod
    def set_notes(cls, key: _KeyT, notes: _DataT) -> None:
        cls.__notes_map[key] = notes

    @classmethod
    @abstractmethod
    def init_notes(cls) -> _DataT:
        ...


@dataclass
class MessageNotes(BaseNotes[Message, "MessageNotes"]):
    trigger: Optional[str] = None

    @classmethod
    def init_notes(cls) -> "MessageNotes":
        return MessageNotes()
