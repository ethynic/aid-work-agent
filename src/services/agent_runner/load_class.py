"""轻重负载判型：配置清单消费与入队/运行时两类判定信号。

清单权威源在 configs/config.yaml agent_runner.heavy_tools/heavy_skills
（判定依据见设计 §4.2 核对表）。判型只影响领取池归属，不影响执行本身；
heavy 误判 light 的后果是重任务在通用池跑完（cgroup 统一 1GiB 不崩溃），
light 误判 heavy 只是占用重池容量，两种误判均可接受。
"""


def skill_base_name(skill):
    """技能基础名：去掉尾段版本号（'interior-render-1.0.0' -> 'interior-render'）。

    版本升级不改判型配置；无版本后缀的原样返回。
    """
    parts = str(skill).rsplit('-', 1)
    if len(parts) == 2 and parts[1] and parts[1].replace('.', '').isdigit():
        return parts[0]
    return str(skill)


def heavy_sets():
    from src.config.settings import settings
    config = settings.agent_runner
    return set(config.heavy_tools or []), set(config.heavy_skills or [])


def classify_profile(profile_id, config):
    """入队判型：子智能体装配白名单 ∩ heavy 清单。

    config 为 None（main profile）判 light——主智能体可用全量工具，
    按可用集判定会恒 heavy 使分离失效；靠运行时重判兜底。
    工具白名单仅在显式 allowed（非 inherit 全量）时参与判定，
    inherit/excluded 形态信息不足以表达"实际会用重工具"。
    """
    if config is None:
        return 'light'
    heavy_tools, heavy_skills = heavy_sets()
    if not heavy_tools and not heavy_skills:
        return 'light'
    try:
        allowed_tools = set(config.get_allowed_tools() or [])
        if allowed_tools & heavy_tools:
            return 'heavy'
        allowed_skills = {skill_base_name(skill)
                          for skill in (config.get_allowed_skills() or [])}
        if allowed_skills & heavy_skills:
            return 'heavy'
    except Exception:
        return 'light'
    return 'light'


def state_uses_heavy(state):
    """运行时重判信号：checkpoint 边界扫描已记录的工具/技能调用。

    state.tools 含本轮全部工具调用事实；技能脚本经 skill_execute 执行，
    重负载依据参数里的技能名判定。
    """
    heavy_tools, heavy_skills = heavy_sets()
    if not heavy_tools and not heavy_skills:
        return False
    for fact in (getattr(state, 'tools', None) or {}).values():
        call = getattr(fact, 'call', None)
        name = getattr(call, 'name', None)
        if not name:
            continue
        if name in heavy_tools:
            return True
        if name == 'skill_execute':
            skill = (getattr(call, 'arguments', None) or {}).get('skill')
            if skill and skill_base_name(skill) in heavy_skills:
                return True
    return False
