"""The one exception type every front end catches -- re-exported as canvas.HistosError."""


class HistosError(Exception):
    """Errores esperables (vault no encontrado, id duplicado, etc.) -- mensaje ya listo para el usuario."""
