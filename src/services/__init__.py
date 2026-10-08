"""Lazy public service exports; importing infrastructure starts no domain service."""

from importlib import import_module

_EXPORTS = {
    "SessionRecordService": "session_record", "SessionRecordManager": "session_record",
    "SentimentService": "sentiment_service", "SentimentResult": "sentiment_service",
    "NotificationService": "notification_service", "NotificationMessage": "notification_service",
    "NotificationChannel": "notification_service", "notification_service": "notification_service",
    "ClassificationService": "classification_service", "ClassificationResult": "classification_service",
    "CaseMatchingService": "case_matching_service", "CaseMatch": "case_matching_service",
    "case_matching_service": "case_matching_service", "XToImageService": "x_to_image",
    "x_to_image_service": "x_to_image", "XToImageInput": "x_to_image", "XToImageResult": "x_to_image",
    "InputType": "x_to_image", "ImageFormat": "x_to_image",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module("." + module, __name__), name)
    globals()[name] = value
    return value
