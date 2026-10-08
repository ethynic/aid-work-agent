"""Accepted execution input, separate from immutable caller intent and UI facts."""

import copy

from .contracts import RunnerError


def project_execution_context(request, profile_id, fingerprint):
    data = copy.deepcopy(request.request_data)
    augmentations = list(request.prompt_augmentations)
    video = data.pop('video_params', None)
    if video is not None:
        if not isinstance(video, dict):
            raise RunnerError('INVALID_VIDEO_PARAMS', 422)
        from src.video_request_context import build_video_profile_request_context
        # Session routing comes from the trusted accepted session, not a domain hint.
        context = build_video_profile_request_context({**video, 'session_id':request.session.session_id},
                                                     profile_id=profile_id)
        if context is not None:
            augmentations.extend(context.prompt_augmentations)
            data['video_params'] = dict(context.request_data['video_params'])
    return {'version':1, 'profile_id':profile_id, 'profile_fingerprint':fingerprint,
            'prompt_augmentations':augmentations, 'request_data':data}
