class MotionError(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details
