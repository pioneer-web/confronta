class ImportacaoError(Exception):
    pass

class SecurityValidationError(ImportacaoError):
    pass

class GISValidationError(ImportacaoError):
    pass

class DatasetIdentityError(ImportacaoError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}

class PromotionError(ImportacaoError):
    pass


class BatchInterruptionRequested(ImportacaoError):
    """Interrupção cooperativa solicitada pelo administrador antes da publicação atômica."""
    pass
