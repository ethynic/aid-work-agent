"""Per-item recruiting continuation; device and model ledgers keep their owners.

Original
invocation results supply images on demand; phase facts contain only stable
references, the server evaluation and small tool output.
"""

import asyncio
import copy
import hashlib

from .domain_artifacts import prepare_image_ref
from .domain_fee import write_domain_fee, invalidate_domain_fee_cache
from .recruiting_writers import write_resume, write_match
from .durable_flow import LocalContinuationRequired


def _failure(code, message):
    return {'success': False, 'code': code, 'message': message}


async def process_item(owner, *, tenant_id, execution_id, tool_call_id,
                       invocation_id, device_id, item_ordinal, payload):
    from src.services import resume_vl_service as vl, recruiting_resume_service as resumes
    from src.services import recruiting_match_service as matching
    from .pricing import RESUME_RECOGNITION_TOOL_NAME, resume_recognition_price

    if not isinstance(payload, dict):
        return _failure('RESUME_PAYLOAD_INVALID', '简历结果必须为对象')
    name = str(payload.get('candidate_name') or '').strip()
    if not name:
        return _failure('RESUME_PAYLOAD_INVALID', 'payload 缺 candidate_name：姓名唯一来源=非截图识别，缺省不评估不入库')
    images = payload.get('images') or []
    stitched = (images[0].get('base64') if isinstance(images, list)
        and images and isinstance(images[0], dict) else None)
    if not isinstance(stitched, str) or not stitched.strip():
        return _failure('RESUME_VL_FAILED', 'payload 缺拼接长图（images[0].base64），云端无法识别')
    origin = {'invocation_id': invocation_id, 'item_ordinal': item_ordinal,
        'candidate_name': name, 'image_digest': hashlib.sha256(stitched.encode()).hexdigest()}
    policy_origin = {**origin,'resume_policy_version':1}
    policy = await owner.saved_domain(branch='resume.policy', ordinal=item_ordinal)
    if policy is None:
        job_ctx = None
        if payload.get('job_id') or payload.get('job_name'):
            job_ctx = await asyncio.to_thread(matching._load_job_context, tenant_id,
                payload.get('job_id'), payload.get('job_name')) or {'job_name': payload.get('job_name')}
        try:
            provider, model = vl.resolve_model_spec()
        except vl.ResumeVLModelError as error:
            return _failure('RESUME_VL_FAILED', f'简历识别模型配置非法：{error}')
        policy_result = await owner.complete_phase(branch='resume.policy', ordinal=item_ordinal,
            intent=policy_origin, result={'job_context': job_ctx, 'credit_cost': resume_recognition_price(),
                'model_spec': provider + '/' + model})
    else:
        if policy['request'] != policy_origin or policy.get('phase') != 'completed':
            raise LocalContinuationRequired('LOCAL_RESUME_POLICY_VERIFICATION_REQUIRED')
        policy_result = policy['result']
    evaluated = await owner.saved_domain(branch='resume.evaluation', ordinal=item_ordinal)
    if evaluated is None:
        try:
            bands = await asyncio.to_thread(vl.slice_stitched_image, stitched)

            async def model_call(index, invoke):
                return await owner.model_phase(branch='resume.evaluate',
                    ordinal=item_ordinal * 2 + index, intent=origin, invoke=invoke,
                    purpose='resume_recognition_covered')

            evaluation = await vl.evaluate_resume(bands, name, policy_result['job_context'],
                model_param=policy_result['model_spec'], model_call=model_call)
        except vl.ResumeVLModelError as error:
            if policy is not None:
                raise LocalContinuationRequired('LOCAL_RESUME_MODEL_CONFIGURATION_CHANGED') from error
            return _failure('RESUME_VL_FAILED', f'简历识别模型配置非法：{error}')
        except vl.ResumeVLError as error:
            # Existing known domain failure remains a per-item business result.
            return _failure('RESUME_VL_FAILED', f'简历云端识别失败：{error}')
        if not vl.resume_name_matches(name, evaluation.get('name_seen') or ''):
            return _failure('RESUME_NAME_MISMATCH', '姓名核对不匹配，已跳过不入库不扣费，请人工核对后重试')
        evaluation['match_threshold'] = (policy_result['job_context'] or {}).get('match_threshold')
        evaluation = await owner.complete_phase(branch='resume.evaluation', ordinal=item_ordinal,
            intent=origin, result=evaluation)
    else:
        if evaluated['request'] != origin or evaluated.get('phase') != 'completed':
            raise LocalContinuationRequired('LOCAL_RESUME_EVALUATION_VERIFICATION_REQUIRED')
        evaluation = evaluated['result']
    fee = await owner.commit_domain(branch='resume.fee', ordinal=item_ordinal,
        intent={**origin, 'billing_tool_name': RESUME_RECOGNITION_TOOL_NAME,
            'credit_cost': policy_result['credit_cost'], 'device_id': device_id}, writer=write_domain_fee)
    invalidate_domain_fee_cache(tenant_id, fee)
    inserted = await owner.saved_domain(branch='resume.insert', ordinal=item_ordinal)
    if inserted is None:
        normalized = copy.deepcopy(payload)
        # No client text alias may become the original record's recognized text.
        for key in ('ocr_text', 'ocr', 'text', 'ocr_chars', 'ocr_accel'):
            normalized.pop(key, None)
        normalized.update(resume_summary=evaluation.get('resume_summary'),
            key_info=evaluation.get('key_info'), ocr_engine=evaluation.get('model') or 'unknown')
        try:
            fields = await asyncio.to_thread(resumes.prepare_resume_record_from_tool_result,
                tenant_id, normalized)
            prepared_images = []
            for index, image in enumerate(fields.pop('images_base64')):
                raw, mime_type, _ = await asyncio.to_thread(
                    resumes.decode_resume_image, image['data'], image['mime_type'])
                prepared_images.append(await asyncio.to_thread(prepare_image_ref, tenant_id,
                    execution_id=execution_id, tool_call_id=tool_call_id,
                    invocation_id=invocation_id, item_ordinal=item_ordinal,
                    image_ordinal=index, content=raw, mime_type=mime_type, name=image.get('name')))
        except OSError:
            # This local image preparation failed before the cursor insert.
            # Preserve the delivered recognition fee and let Batch try its
            # next original item; this is not a checkpoint/lease failure.
            return _failure('RESUME_STORE_FAILED', '简历图片保存失败，请核对存储后重试')
        except (resumes.ResumePayloadError, ValueError) as error:
            if str(error).startswith('LOCAL_ARTIFACT_'):
                return _failure('RESUME_STORE_FAILED', '简历图片保存失败，已保留原文件，请核对存储后重试')
            return _failure('RESUME_PAYLOAD_INVALID', str(error))
        # Images are stable owned refs, never base64 copies in a phase request.
        fields['images'] = prepared_images
        inserted_result = await owner.commit_domain(branch='resume.insert', ordinal=item_ordinal,
            intent={**origin, **fields}, writer=write_resume)
        if fields.get('job_warning') and inserted_result.get('applied'):
            inserted_result = {**inserted_result, 'warning': fields['job_warning']}
    else:
        if (any(inserted['request'].get(key) != value for key, value in origin.items())
                or inserted.get('phase') != 'completed'):
            raise LocalContinuationRequired('LOCAL_RESUME_INSERT_VERIFICATION_REQUIRED')
        inserted_result = inserted['result']
        if inserted['request'].get('job_warning') and inserted_result.get('applied'):
            inserted_result = {**inserted_result, 'warning': inserted['request']['job_warning']}
    if not inserted_result.get('applied'):
        return _failure('RESUME_STORE_FAILED', '简历读取成功但入库失败，请稍后重试或联系管理员')
    matched = await owner.commit_domain(branch='resume.match', ordinal=item_ordinal,
        intent={**origin, 'resume_id': inserted_result['resume_id'], 'score': evaluation.get('score'),
            'match_threshold': evaluation.get('match_threshold'),
            'match_summary': evaluation.get('match_summary'), 'key_info': evaluation.get('key_info')},
        writer=write_match)
    summary = {key: value for key, value in inserted_result.items() if key != 'applied'}
    summary.update({key: matched.get(key) for key in ('match_score', 'match_status', 'match_summary')})
    if summary['match_score'] is None:
        summary['match_note'] = '未评分'
    return {'success': True, 'data': summary}
