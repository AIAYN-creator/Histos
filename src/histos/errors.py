"""The one exception type every front end catches -- re-exported as canvas.HistosError."""


class HistosError(Exception):
    """Expected errors (vault not found, duplicate id...) -- the message is ready for the user as is."""
