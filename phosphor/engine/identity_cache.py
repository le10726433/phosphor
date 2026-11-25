"""A strong identity key for bounded functools.lru_cache instances."""


class Identity:
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return id(self.value)

    def __eq__(self, other):
        return isinstance(other, Identity) and self.value is other.value
