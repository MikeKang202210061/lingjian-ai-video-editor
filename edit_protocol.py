"""Versioned, validated edit plans and controlled timeline mutations.

The AI may propose decisions, but only this module is allowed to turn those
decisions into project mutations.  All functions are deterministic and do not
call a model or touch source media.
"""
from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


PLAN_SCHEMA_VERSION = 1
ALLOWED_ROLES = {"hook", "setup", "development", "climax", "outro"}
ALLOWED_TRANSITIONS = {
    "none", "fade", "dissolve", "wipe_left", "wipe_right",
    "slide_left", "slide_right", "circle", "smooth", "pip_zoom",
    "tear_left", "tear_right", "pixelize", "squeeze", "radial",
    "fade_black", "fade_white", "cover_left", "reveal_right",
}
ALLOWED_MASKS = {
    "none", "spotlight", "ellipse", "portrait_card", "cinema", "diamond",
    "vertical_strip", "split_left", "split_right", "privacy_blur", "vignette",
}

OPENING_PRESETS = {
    "hollow_vlog": {
        "name": "镂空 VLOG · 字内走马灯",
        "summary": "远景背景保持正常播放，中央 VLOG 字形成为透明画中画窗口，内部巡游全部素材并同步滴答。",
        "text_window": True,
        "beats": [],
    },
    "bounce_time": {
        "name": "Q 弹时光 · 滴答回溯",
        "summary": "中央果冻感标题弹入并衰减回弹，前三个镜头以聚焦、分屏和画中画配合滴答进入短篇。",
        "bounce_title": True,
        "beats": [
            ("spotlight", "none", .50, .50, .72, .66, 28, "Q 弹标题落位并聚焦主体"),
            ("split_left", "cover_left", .50, .50, .52, .90, 12, "滴答推进第一次时间回溯"),
            ("portrait_card", "pip_zoom", .50, .50, .74, .72, 16, "画中画放大进入短篇正文"),
        ],
    },
    "carousel_flash": {
        "name": "走马灯闪回 · 全素材巡游",
        "summary": "从每个导入素材各取一个短瞬间，窗格左右巡游并快速闪回，随后按真实拍摄顺序进入正文。",
        "montage": True,
        "beats": [],
    },
    "cinematic_window": {
        "name": "电影窗格 · Vlog 高级开篇",
        "summary": "先用悬浮窗格保留环境，再放大进入全屏；适合旅行、生活记录和人物出场。",
        "beats": [
            ("portrait_card", "none", .50, .50, .78, .72, 18, "窗格建立地点与人物"),
            ("cinema", "pip_zoom", .50, .50, .88, .76, 12, "画中画放大进入正文"),
            ("none", "dissolve", .50, .50, .72, .72, 24, "回到干净全屏叙事"),
        ],
    },
    "split_rhythm": {
        "name": "动态分屏 · 节奏快切",
        "summary": "窄条预告、左右分屏、全屏释放，适合多个细节镜头和音乐重拍。",
        "beats": [
            ("vertical_strip", "none", .50, .50, .42, .90, 10, "窄条只透露关键细节"),
            ("split_left", "cover_left", .50, .50, .52, .92, 12, "左侧分屏补充第二信息"),
            ("split_right", "reveal_right", .50, .50, .52, .92, 12, "右侧揭示后进入全屏"),
        ],
    },
    "subject_reveal": {
        "name": "主体揭示 · 聚焦蒙版",
        "summary": "先隐藏杂乱背景，只展示人物或产品，再用几何蒙版揭示空间。",
        "beats": [
            ("spotlight", "none", .50, .48, .62, .62, 34, "柔和聚焦主体"),
            ("diamond", "circle", .50, .50, .76, .70, 22, "几何展开揭示环境"),
            ("none", "smooth", .50, .50, .72, .72, 24, "平滑进入故事正文"),
        ],
    },
    "tear_flash": {
        "name": "撕裂闪回 · 强节拍开场",
        "summary": "暗角压住信息，以两次方向相反的数字撕裂闪回制造冲击。",
        "beats": [
            ("vignette", "none", .50, .50, .72, .72, 20, "暗角蓄力并保留标题空间"),
            ("cinema", "tear_left", .50, .50, .72, .72, 18, "向左撕裂闪出结果镜头"),
            ("none", "tear_right", .50, .50, .72, .72, 18, "反向撕裂回到事件起点"),
        ],
    },
    "minimal_film": {
        "name": "极简电影 · 克制留白",
        "summary": "宽银幕建立气氛，淡黑切换章节，适合纪录、风景和慢节奏 Vlog。",
        "beats": [
            ("cinema", "none", .50, .50, .72, .72, 16, "宽银幕建立情绪与地点"),
            ("cinema", "fade_black", .50, .50, .72, .72, 16, "经黑场进入标题或目标"),
            ("vignette", "dissolve", .50, .50, .72, .72, 22, "轻暗角承接正文"),
        ],
    },
}


def opening_preset_choices() -> list[tuple[str, str]]:
    return [("AI 智能推荐", "smart")] + [(v["name"], k) for k, v in OPENING_PRESETS.items()]


def recommend_opening_preset(story_style: str = "") -> str:
    text = str(story_style or "")
    if "美食" in text or "产品" in text:
        return "subject_reveal"
    if "剧情" in text:
        return "tear_flash"
    if "纪录" in text or "电影" in text:
        return "minimal_film"
    if "口播" in text or "知识" in text:
        return "split_rhythm"
    return "cinematic_window"


def apply_opening_treatment(plan: dict, requested: str = "smart", story_style: str = "") -> dict:
    """Return a plan whose opening masks/transitions are concrete and renderable."""
    result = copy.deepcopy(plan)
    preset_id = requested if requested in OPENING_PRESETS else recommend_opening_preset(story_style)
    preset = OPENING_PRESETS[preset_id]
    decisions = result.get("decisions") or []
    shot_plan = []
    if preset.get("text_window"):
        catalog=list(result.get("source_catalog") or [])
        if not catalog:
            seen=set()
            for item in decisions:
                path=str(item.get("source_path") or "")
                if path and path not in seen:
                    seen.add(path);catalog.append({"source_index":item.get("source_index",-1),"source_path":path,"source_name":item.get("source_name",""),"source_duration":item.get("source_duration",0),"capture_order":item.get("capture_order","")})
        fill_segments=[];flash_duration=max(.32,min(.85,4.2/max(1,len(catalog))))
        for source in catalog:
            path=str(source.get("source_path") or "");matches=[x for x in decisions if str(x.get("source_path") or "")==path];duration=float(source.get("source_duration",0) or 0)
            if matches:
                chosen=matches[0];available=max(0.,float(chosen.get("end",0))-float(chosen.get("start",0)));piece=min(flash_duration,available) if available else flash_duration;start=float(chosen.get("start",0))+max(0.,(available-piece)*.5)
            else:piece=min(flash_duration,duration) if duration else flash_duration;start=max(0.,min(max(0.,duration-piece),duration*.18))
            if piece>=.12:fill_segments.append({"path":path,"start":round(start,3),"duration":round(piece,3),"name":str(source.get("source_name") or Path(path).name)})
        if decisions and fill_segments:
            base=decisions[0];title_duration=sum(float(x["duration"]) for x in fill_segments);base_start=float(base.get("start",0));source_duration=float(base.get("source_duration",0) or base_start+title_duration);title_duration=min(title_duration,max(.2,source_duration-base_start))
            title={**copy.deepcopy(base),"decision_id":str(uuid.uuid4()),"ordinal":0,"start":round(base_start,3),"end":round(base_start+title_duration,3),"role":"hook","caption":"","reason":"镂空 VLOG 字内播放全素材走马灯","transition":"none","mask_shape":"none","title_effect":"text_window","title_text":"VLOG","title_fill_segments":fill_segments,"creative_purpose":"背景远景保持正常，只有字形内部切换素材"}
            for item in decisions:
                if item.get("role")=="hook":item["role"]="setup"
            decisions=[title]+decisions
            for i,item in enumerate(decisions):item["ordinal"]=i
            result["decisions"]=decisions;shot_plan.append({"ordinal":0,"mask":"text_alpha_matte","transition":"none","purpose":title["creative_purpose"],"source_name":title.get("source_name","")})
    elif preset.get("montage"):
        catalog = list(result.get("source_catalog") or [])
        if not catalog:
            seen = set()
            for item in decisions:
                path = str(item.get("source_path") or "")
                if path and path not in seen:
                    seen.add(path); catalog.append({
                        "source_path": path, "source_name": item.get("source_name", ""),
                        "source_duration": item.get("source_duration", 0),
                        "capture_order": item.get("capture_order", ""),
                    })
        flash_duration = max(.18, min(.52, 4.2 / max(1, len(catalog))))
        transitions = ("none", "cover_left", "slide_left", "tear_left", "pip_zoom", "tear_right")
        positions = (.28, .50, .72, .50)
        teasers = []
        for i, source in enumerate(catalog):
            path = str(source.get("source_path") or "")
            matches = [x for x in decisions if str(x.get("source_path") or "") == path]
            if matches:
                chosen = matches[0]; source_duration = float(chosen.get("source_duration", source.get("source_duration", 0)) or 0)
                available = max(0., float(chosen.get("end", 0)) - float(chosen.get("start", 0)))
                duration = min(flash_duration, available) if available else flash_duration
                start = float(chosen.get("start", 0)) + max(0., (available-duration)*.5)
            else:
                source_duration = float(source.get("source_duration", 0) or 0)
                duration = min(flash_duration, source_duration) if source_duration else flash_duration
                start = max(0., min(max(0., source_duration-duration), source_duration*.18))
            if duration < .12:
                continue
            transition = transitions[i % len(transitions)] if i else "none"
            mask = "portrait_card" if i % 4 != 3 else "vertical_strip"
            teaser = {
                "decision_id": str(uuid.uuid4()), "ordinal": i,
                "source_index": int(source.get("source_index", matches[0].get("source_index", -1) if matches else -1)),
                "source_path": path, "source_name": str(source.get("source_name") or Path(path).name),
                "source_duration": source_duration, "capture_order": str(source.get("capture_order") or ""),
                "start": round(start, 3), "end": round(start+duration, 3), "role": "hook",
                "caption": "快速预览" if i == 0 else "", "reason": f"走马灯闪回：素材 {i+1}/{len(catalog)}",
                "transition": transition, "mask_shape": mask,
                "mask_x": positions[i % len(positions)], "mask_y": .5,
                "mask_width": .66 if mask == "portrait_card" else .34, "mask_height": .78,
                "mask_feather": 10., "mask_opacity": 1.,
                "creative_purpose": "每个素材各取一闪，快速建立本片视觉地图",
            }
            teasers.append(teaser); shot_plan.append({
                "ordinal": i, "mask": mask, "transition": transition,
                "purpose": teaser["reason"], "source_name": teaser["source_name"],
            })
        for item in decisions:
            if item.get("role") == "hook": item["role"] = "setup"
        decisions = teasers + decisions
        for i, item in enumerate(decisions): item["ordinal"] = i
        result["decisions"] = decisions
    for i, item in enumerate(decisions):
        item.setdefault("mask_shape", "none")
        item.setdefault("mask_x", .5); item.setdefault("mask_y", .5)
        item.setdefault("mask_width", .72); item.setdefault("mask_height", .72)
        item.setdefault("mask_feather", 24.); item.setdefault("mask_opacity", 1.)
        if preset.get("montage") or preset.get("text_window") or i >= len(preset["beats"]):
            continue
        mask, transition, x, y, width, height, feather, purpose = preset["beats"][i]
        item.update({
            "mask_shape": mask, "mask_x": x, "mask_y": y,
            "mask_width": width, "mask_height": height,
            "mask_feather": float(feather), "mask_opacity": 1.,
            "transition": transition if i else "none",
            "creative_purpose": purpose,
        })
        shot_plan.append({
            "ordinal": i, "mask": mask, "transition": item["transition"],
            "purpose": purpose, "source_name": item.get("source_name", ""),
        })
    if preset.get("bounce_title") and decisions:
        decisions[0]["title_effect"]="bounce";decisions[0]["title_text"]="时光回溯";decisions[0]["title_color"]="#FFD43B"
        if shot_plan:shot_plan[0]["purpose"]="Q 弹标题按弹簧曲线衰减回弹，随后滴答回溯"
    cue_ids = {
        "cinematic_window": ("impact", "whoosh", "sparkle"),
        "split_rhythm": ("clock_tick", "swipe", "pop"),
        "subject_reveal": ("pop", "sparkle", "whoosh"),
        "tear_flash": ("impact", "paper_tear", "glitch"),
        "minimal_film": ("impact", "whoosh", "sparkle"),
        "bounce_time": ("clock_tick", "clock_tock", "pop"),
    }
    sound_cues=[];cursor=0.
    sound_items=(decisions[0].get("title_fill_segments",[]) if preset.get("text_window") and decisions else decisions[:len(shot_plan)]) if (preset.get("montage") or preset.get("text_window")) else decisions[:min(3,len(decisions))]
    for i,item in enumerate(sound_items):
        effect_id=("clock_tick" if i%2==0 else "clock_tock") if (preset.get("montage") or preset.get("text_window")) else cue_ids.get(preset_id,("whoosh",))[i%len(cue_ids.get(preset_id,("whoosh",)))]
        sound_cues.append({"effect_id":effect_id,"start":round(cursor,3),"volume":.72 if effect_id.startswith("clock_") else .62,"reason":f"开篇镜头 {i+1} 节拍"})
        cursor+=float(item.get("duration",0)) if preset.get("text_window") else max(0.,float(item.get("end",0))-float(item.get("start",0)))
    result["sound_cues"]=sound_cues
    result["creative_direction"] = {
        "preset_id": preset_id,
        "requested": requested,
        "name": preset["name"],
        "summary": preset["summary"],
        "duration_hint": round((float(decisions[0].get("end",0))-float(decisions[0].get("start",0))) if preset.get("text_window") and decisions else sum(max(0., float(x.get("end", 0))-float(x.get("start", 0))) for x in (decisions[:len(shot_plan)] if preset.get("montage") else decisions[:3])), 2),
        "shots": shot_plan,
        "sounds": sound_cues,
    }
    layer_counts={"bounce_time":2,"carousel_flash":2,"split_rhythm":2,"cinematic_window":1,"subject_reveal":1,"tear_flash":1}
    overlays=[];layer_count=layer_counts.get(preset_id,0)
    if decisions and layer_count:
        hook_duration=max(.3,float(decisions[0].get("end",0))-float(decisions[0].get("start",0)))
        candidates=[];seen=set()
        for item in decisions[1:]:
            path=str(item.get("source_path") or "")
            if path and path not in seen:seen.add(path);candidates.append(item)
        for i,item in enumerate(candidates[:layer_count]):
            timeline_start=.10+i*.18;available=max(.12,float(item.get("end",0))-float(item.get("start",0)));duration=min(.82,available,max(.12,hook_duration-timeline_start))
            overlays.append({"id":str(uuid.uuid4()),"track":2+i,"source_path":item.get("source_path"),"source_name":item.get("source_name"),"start":float(item.get("start",0)),"end":round(float(item.get("start",0))+duration,3),"timeline_start":round(timeline_start,3),"layout":"pip_left" if i%2 else "pip_right","mask_shape":"ellipse" if i%2==0 else "none","x":.78 if i%2==0 else .22,"y":.18 if i%2==0 else .66,"width":.32 if i==0 else .28,"height":.24 if i==0 else .21,"feather":10.,"opacity":.96,"border":True,"role":"ai_mask_layer","reason":f"AI 为 {preset['name']} 创建独立 V{2+i} 合成层，并避让中央标题与底部字幕安全区"})
    result["overlay_tracks"]=overlays
    result["creative_direction"]["overlay_count"]=len(overlays)
    result["validation"] = validate_edit_plan(result)
    return result
ALLOWED_TOOLS = {
    "trim_clip", "remove_clip", "move_clip_with_order_guard", "set_caption",
    "set_transition", "set_mask", "set_volume",
}


def capture_key(path: str, start: float = 0.0) -> tuple[str, float, str]:
    """Return a stable ordering key, preferring camera filename timestamps."""
    name = Path(path or "").name
    match = re.search(r"(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)", name)
    stamp = "".join(match.groups()) if match else ""
    return stamp, round(float(start), 3), name.lower()


def _source_meta(analyses: list[dict], index: int) -> dict:
    if not 0 <= index < len(analyses):
        raise ValueError(f"未知素材索引：{index}")
    return dict(analyses[index].get("meta") or {})


def build_edit_plan(sequence: list[dict], analyses: list[dict], target: float,
                    prompt: str = "", engine: str = "cloud") -> dict:
    """Convert an AI sequence into an auditable, versioned edit plan."""
    decisions = []
    for i, raw in enumerate(sequence or []):
        source_index = int(raw.get("source_index", -1))
        meta = _source_meta(analyses, source_index)
        start = round(float(raw.get("start", 0)), 3)
        end = round(float(raw.get("end", 0)), 3)
        transition = str(raw.get("transition", "none")).replace("cut", "none")
        decisions.append({
            "decision_id": str(uuid.uuid4()),
            "ordinal": i,
            "source_index": source_index,
            "source_path": str(meta.get("path") or ""),
            "source_name": str(meta.get("name") or Path(str(meta.get("path") or "")).name),
            "source_duration": round(float(meta.get("duration", 0)), 3),
            "capture_order": str(meta.get("capture_order") or capture_key(str(meta.get("path") or ""))[0]),
            "start": start,
            "end": end,
            "role": str(raw.get("role") or ("hook" if i == 0 else "development")),
            "caption": str(raw.get("caption") or "").strip(),
            "reason": str(raw.get("reason") or "AI 选择的有效镜头").strip(),
            "transition": transition,
            "mask_shape": str(raw.get("mask_shape") or "none"),
            "mask_x": float(raw.get("mask_x", .5)),
            "mask_y": float(raw.get("mask_y", .5)),
            "mask_width": float(raw.get("mask_width", .72)),
            "mask_height": float(raw.get("mask_height", .72)),
            "mask_feather": float(raw.get("mask_feather", 24.)),
            "mask_opacity": float(raw.get("mask_opacity", 1.)),
            "title_effect": str(raw.get("title_effect") or ""),
            "title_text": str(raw.get("title_text") or ""),
            "title_fill_segments": copy.deepcopy(raw.get("title_fill_segments") or []),
            "title_color": str(raw.get("title_color") or "#FFFFFF"),
        })
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "prompt": str(prompt or ""),
        "target_duration": round(float(target), 3),
        "capture_order_locked": should_lock_capture_order(decisions, prompt, target),
        "source_catalog": [{
            "source_index": i,
            "source_path": str((item.get("meta") or {}).get("path") or ""),
            "source_name": str((item.get("meta") or {}).get("name") or Path(str((item.get("meta") or {}).get("path") or "")).name),
            "source_duration": round(float((item.get("meta") or {}).get("duration", 0)), 3),
            "capture_order": str((item.get("meta") or {}).get("capture_order") or capture_key(str((item.get("meta") or {}).get("path") or ""))[0]),
        } for i, item in enumerate(analyses)],
        "decisions": decisions,
    }
    plan["validation"] = validate_edit_plan(plan)
    return plan


def should_lock_capture_order(decisions: list[dict], prompt: str = "", target: float = 0) -> bool:
    text = str(prompt or "").lower()
    explicit = any(x in text for x in ("按顺序拍", "拍摄顺序", "真实顺序", "时间顺序", "严格顺序", "chronological"))
    stamps = [str(x.get("capture_order") or "") for x in decisions]
    reliable = len(set(stamps)) >= 2 and all(re.fullmatch(r"\d{14}", x) for x in stamps if x)
    return bool(explicit or target >= 60 or reliable)


def auto_repair_capture_order(plan:dict)->list[str]:
    """Stably repair AI body chronology while preserving all leading creative hooks."""
    if not plan.get("capture_order_locked"):return list(plan.get("auto_repairs") or [])
    decisions=list(plan.get("decisions") or [])
    first_body=0
    while first_body<len(decisions) and decisions[first_body].get("role")=="hook":first_body+=1
    body=decisions[first_body:]
    if len(body)<2:return list(plan.get("auto_repairs") or [])
    original=list(body)
    indexed=list(enumerate(body))
    def key(pair):
        index,item=pair;stamp=str(item.get("capture_order") or "")
        return (0,stamp,float(item.get("start",0)),index) if stamp else (1,"",0.,index)
    repaired=[item for _,item in sorted(indexed,key=key)]
    if [id(x) for x in repaired]==[id(x) for x in original]:return list(plan.get("auto_repairs") or [])
    old_position={str(item.get("decision_id") or id(item)):i for i,item in enumerate(original)}
    moved=[]
    for new_index,item in enumerate(repaired):
        old_index=old_position[str(item.get("decision_id") or id(item))]
        if old_index!=new_index:moved.append((item,old_index+first_body+1,new_index+first_body+1))
    decisions[first_body:]=repaired
    for i,item in enumerate(decisions):item["ordinal"]=i
    plan["decisions"]=decisions
    names=[]
    for item,old,new in moved[:6]:names.append(f"{item.get('source_name') or Path(str(item.get('source_path') or '')).name}（{old}→{new}）")
    message=f"已自动按真实拍摄时间修复正文顺序：移动 {len(moved)} 个镜头"
    if names:message+="；"+"、".join(names)
    if len(moved)>6:message+=f" 等 {len(moved)} 个"
    repairs=list(plan.get("auto_repairs") or [])
    if message not in repairs:repairs.append(message)
    plan["auto_repairs"]=repairs
    return repairs


def validate_edit_plan(plan: dict) -> dict:
    blockers, warnings = [], []
    repairs=auto_repair_capture_order(plan)
    if int(plan.get("schema_version", 0)) != PLAN_SCHEMA_VERSION:
        blockers.append("不支持的 AI 剪辑方案版本")
    decisions = list(plan.get("decisions") or [])
    if not decisions:
        blockers.append("AI 剪辑方案为空")
        return {"ok": False, "blockers": blockers, "warnings": warnings, "repairs": repairs,
                "duration": 0.0, "chronology_ratio": 1.0}

    seen_ranges = set()
    for i, item in enumerate(decisions):
        start, end = float(item.get("start", -1)), float(item.get("end", -1))
        source_duration = float(item.get("source_duration", 0))
        if start < 0 or end <= start:
            blockers.append(f"第 {i + 1} 个镜头入点/出点无效")
        if source_duration and end > source_duration + .04:
            blockers.append(f"第 {i + 1} 个镜头超出原素材时长")
        if end - start < .45:
            warnings.append(f"第 {i + 1} 个镜头短于 0.45 秒，可能造成闪切")
        role = str(item.get("role") or "")
        if role not in ALLOWED_ROLES:
            item["role"] = "development"; repairs.append(f"第 {i + 1} 个镜头角色已降级为 development")
        transition = str(item.get("transition") or "none")
        if transition not in ALLOWED_TRANSITIONS:
            item["transition"] = "none"; repairs.append(f"第 {i + 1} 个镜头未知转场已降级为直接切换")
        mask = str(item.get("mask_shape") or "none")
        if mask not in ALLOWED_MASKS:
            item["mask_shape"] = "none"; repairs.append(f"第 {i + 1} 个镜头未知蒙版已关闭")
        for key, default in (("mask_x", .5), ("mask_y", .5), ("mask_width", .72),
                             ("mask_height", .72), ("mask_opacity", 1.)):
            item[key] = max(0., min(1., float(item.get(key, default))))
        item["mask_feather"] = max(1., min(120., float(item.get("mask_feather", 24.))))
        key = (item.get("source_path"), round(start, 2), round(end, 2))
        if key in seen_ranges:
            blockers.append(f"第 {i + 1} 个镜头与前面镜头完全重复")
        seen_ranges.add(key)

    first_body = 0
    while first_body < len(decisions) and decisions[first_body].get("role") == "hook":
        first_body += 1
    body = decisions[first_body:]
    pairs = list(zip(body, body[1:]))
    comparable = [(a, b) for a, b in pairs if a.get("capture_order") and b.get("capture_order")]
    chronological = sum(
        (str(a.get("capture_order")), float(a.get("start", 0))) <=
        (str(b.get("capture_order")), float(b.get("start", 0))) for a, b in comparable
    )
    ratio = chronological / len(comparable) if comparable else 1.0
    if plan.get("capture_order_locked") and ratio < 1:
        blockers.append("钩子后的正文仍无法满足真实拍摄顺序，请检查缺失的拍摄时间")

    total = round(sum(max(0, float(x.get("end", 0)) - float(x.get("start", 0))) for x in decisions), 3)
    target = max(.1, float(plan.get("target_duration", total)))
    if total < target * .72:
        warnings.append(f"方案只有 {total:.1f} 秒，明显短于目标 {target:.1f} 秒")
    expressive = sum(str(x.get("transition", "none")) not in ("none", "fade", "dissolve") for x in decisions[1:])
    if expressive > max(1, len(decisions) // 5):
        warnings.append("花式转场比例偏高，建议只保留有动作或空间依据的切点")
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings, "repairs": repairs,
            "duration": total, "chronology_ratio": round(ratio, 4)}


def plan_to_clips(plan: dict, clip_class) -> list:
    report = validate_edit_plan(plan)
    if not report["ok"]:
        raise ValueError("；".join(report["blockers"]))
    clips = []
    for item in plan["decisions"]:
        clip = clip_class(
            item["source_path"], item["start"], item["end"], item["source_name"],
            item.get("caption", ""), "bottom", item.get("transition", "none"), 1.0, True,
        )
        clip.role = item.get("role", "development")
        clip.reason = item.get("reason", "")
        clip.capture_order = item.get("capture_order", "")
        clip.ai_selected = True
        clip.mask_shape = item.get("mask_shape", "none")
        clip.mask_x = float(item.get("mask_x", .5)); clip.mask_y = float(item.get("mask_y", .5))
        clip.mask_width = float(item.get("mask_width", .72)); clip.mask_height = float(item.get("mask_height", .72))
        clip.mask_feather = float(item.get("mask_feather", 24.)); clip.mask_opacity = float(item.get("mask_opacity", 1.))
        clip.title_effect = item.get("title_effect", ""); clip.title_text = item.get("title_text", "")
        clip.title_fill_segments = copy.deepcopy(item.get("title_fill_segments") or []); clip.title_color = item.get("title_color", "#FFFFFF")
        clip.caption_effect = item.get("caption_effect", "clean"); clip.motion_effect = item.get("motion_effect", "none")
        clip.resolve_treatment = copy.deepcopy(item.get("resolve_treatment") or {})
        if item.get("caption_font"): clip.caption_font = item["caption_font"]
        if item.get("caption_color"): clip.caption_color = item["caption_color"]
        clips.append(clip)
    return clips


def plan_to_overlays(plan:dict,overlay_class)->list:
    overlays=[]
    for item in plan.get("overlay_tracks") or []:
        overlays.append(overlay_class(path=str(item.get("source_path") or ""),start=float(item.get("start",0)),end=float(item.get("end",0)),timeline_start=float(item.get("timeline_start",0)),name=str(item.get("source_name") or Path(str(item.get("source_path") or "")).name),track=int(item.get("track",2)),layout=str(item.get("layout") or "pip_right"),mask_shape=str(item.get("mask_shape") or "none"),x=float(item.get("x",.76)),y=float(item.get("y",.26)),width=float(item.get("width",.36)),height=float(item.get("height",.30)),feather=float(item.get("feather",10)),opacity=float(item.get("opacity",1)),border=bool(item.get("border",True)),role=str(item.get("role") or "overlay"),reason=str(item.get("reason") or ""),ai_selected=True))
    return overlays


def plan_preview_text(plan: dict) -> str:
    report = plan.get("validation") or validate_edit_plan(plan)
    status = "可以应用" if report["ok"] else "已阻止"
    lines = [
        f"AI 剪辑方案 {plan.get('plan_id', '')[:8]} · {status}",
        f"预计 {report['duration']:.1f} 秒 · {len(plan.get('decisions') or [])} 个片段 · "
        f"正文顺序一致率 {report['chronology_ratio'] * 100:.0f}%",
        "拍摄顺序锁：" + ("开启（钩子后不可倒序）" if plan.get("capture_order_locked") else "关闭"),
    ]
    if report["blockers"]:
        lines.append("\n阻止原因：\n- " + "\n- ".join(report["blockers"]))
    if report["warnings"]:
        lines.append("\n提醒：\n- " + "\n- ".join(report["warnings"]))
    if report["repairs"]:
        lines.append("\n自动修复：\n- " + "\n- ".join(report["repairs"]))
    creative = plan.get("creative_direction") or {}
    if creative:
        lines.append(
            f"\n高级开篇：{creative.get('name')} · 约 {float(creative.get('duration_hint', 0)):.1f} 秒\n"
            f"{creative.get('summary', '')}"
        )
        for shot in creative.get("shots") or []:
            transition = shot.get("transition") or "none"
            lines.append(
                f"  开篇镜头 {int(shot.get('ordinal', 0)) + 1}：蒙版 {shot.get('mask', 'none')} · "
                f"转场 {transition} · {shot.get('purpose', '')}"
            )
        if creative.get("sounds"):
            lines.append("  自动音效：" + "、".join(
                f"{x.get('effect_id')}@{float(x.get('start',0)):.2f}s" for x in creative.get("sounds") or []
            ))
    overlays=plan.get("overlay_tracks") or []
    if overlays:
        lines.append(f"  AI 多轨合成：{len(overlays)} 个独立叠加层")
        for item in overlays:
            lines.append(f"    V{int(item.get('track',2))} · {item.get('source_name')} · 时间线 {float(item.get('timeline_start',0)):.2f}s · {item.get('mask_shape','none')} · {item.get('reason','')}")
    global_direction=plan.get("global_creative_direction") or {}
    if global_direction:
        lines.append(f"\n全片创意编排：{int(global_direction.get('accent_count',0))} 个重点事件 · 最小间隔 {float(global_direction.get('minimum_accent_spacing',0)):.1f}s · 自动配乐 {global_direction.get('music_id','')}")
        lines.append("  " + str(global_direction.get("rule", "")))
        for event in global_direction.get("events") or []:
            if event.get("accent"):
                lines.append(f"  {float(event.get('timeline_start',0)):06.2f}s · {event.get('event')} · {event.get('motion')} · 字幕 {event.get('caption_effect')}")
    from editing_skills import skill_summary
    if plan.get('editing_skill'):
        lines.append(skill_summary(plan))
    lines.append("\n镜头方案：")
    for i, item in enumerate(plan.get("decisions") or []):
        lines.append(
            f"{i + 1:02d}. [{item.get('role')}] {item.get('source_name')} "
            f"{float(item.get('start', 0)):.2f}-{float(item.get('end', 0)):.2f}s · "
            f"{item.get('reason') or '有效镜头'} · 蒙版 {item.get('mask_shape', 'none')} · "
            f"转场 {item.get('transition', 'none')}" +
            (f" · 动效 {item.get('motion_effect')} · 字效 {item.get('caption_effect')}" if item.get('motion_effect') not in (None,'none') else "") +
            (f" · 个性标题 {item.get('title_effect')}「{item.get('title_text','')}」" if item.get('title_effect') else "")
        )
    return "\n".join(lines)


def project_quality_gate(project, source_durations: dict | None = None) -> dict:
    """Mechanical pre-export QA. Taste remains a creator decision."""
    blockers, warnings = [], []
    clips = list(getattr(project, "clips", []) or [])
    if not clips:
        blockers.append("时间线为空")
        return {"ok": False, "blockers": blockers, "warnings": warnings, "score": 0}
    source_durations = source_durations or {}
    for i, clip in enumerate(clips):
        duration = float(clip.end) - float(clip.start)
        if duration <= .08:
            blockers.append(f"第 {i + 1} 个片段长度无效")
        limit = float(source_durations.get(clip.path, 0) or 0)
        if limit and float(clip.end) > limit + .04:
            blockers.append(f"第 {i + 1} 个片段超出原素材")
        caption = str(getattr(clip, "caption", "") or "").strip()
        if caption:
            chars = len(re.sub(r"\s+", "", caption))
            cps = chars / max(.1, duration)
            if cps > 9:
                blockers.append(f"第 {i + 1} 个字幕约 {cps:.1f} 字/秒，不可读")
            elif cps > 6:
                warnings.append(f"第 {i + 1} 个字幕约 {cps:.1f} 字/秒，建议延长或精简")
        td = float(getattr(clip, "transition_duration", .35))
        if i and getattr(clip, "transition", "none") != "none" and td > duration * .35:
            blockers.append(f"第 {i + 1} 个转场时间超过片段的 35%")
    overlays=list(getattr(project,"overlays",[]) or [])
    for i,overlay in enumerate(overlays):
        try:overlay.validate()
        except Exception as exc:blockers.append(f"V{getattr(overlay,'track',2)} 第 {i+1} 个叠加层无效：{exc}");continue
        limit=float(source_durations.get(overlay.path,0) or 0)
        if limit and float(overlay.end)>limit+.04:blockers.append(f"V{overlay.track} 第 {i+1} 个叠加层超出原素材")
        if float(overlay.timeline_start)>=float(getattr(project,"duration",0))+.04:warnings.append(f"V{overlay.track} 第 {i+1} 个叠加层位于成片结束之后")
    if len({int(x.track) for x in overlays})>4:warnings.append("可见叠加视频轨超过 4 条，建议确认画面信息是否过载")

    body = clips[1:] if clips and getattr(clips[0], "role", "") == "hook" else clips
    keys = [capture_key(c.path, c.start) for c in body]
    comparable = [(a, b) for a, b in zip(keys, keys[1:]) if a[0] and b[0]]
    order_locked = bool((getattr(project, "edit_plan", {}) or {}).get("capture_order_locked"))
    if order_locked and comparable and any(a > b for a, b in comparable):
        blockers.append("正文存在真实拍摄顺序倒退；请重排后再导出")
    expressive = sum(getattr(c, "transition", "none") not in ("none", "fade", "dissolve") for c in clips[1:])
    if expressive > max(1, len(clips) // 5):
        warnings.append("花式转场超过约 20%，可能破坏叙事连续性")
    if len(clips) >= 12 and float(getattr(project, "duration", 0)) >= 60:
        chunk = max(1, len(clips) // 4)
        if any(len({Path(c.path).name for c in clips[i:i + chunk]}) == 1 for i in range(0, len(clips), chunk)):
            warnings.append("长时间线部分章节长期只使用单一素材，请确认信息没有停滞")
    deductions = len(blockers) * 30 + len(warnings) * 6
    return {"ok": not blockers, "blockers": blockers, "warnings": warnings,
            "score": max(0, 100 - deductions)}


def apply_controlled_tool(project, tool: str, arguments: dict,
                          capture_order_locked: bool = True) -> dict:
    """Apply one validated mutation and return a reversible before/after record."""
    if tool not in ALLOWED_TOOLS:
        raise ValueError(f"不允许的编辑工具：{tool}")
    before = copy.deepcopy(project.to_dict())
    clip_id = str(arguments.get("clip_id") or "")
    index = next((i for i, c in enumerate(project.clips) if c.id == clip_id), -1)
    if tool != "move_clip_with_order_guard" and index < 0:
        raise ValueError("找不到目标片段")
    if tool == "trim_clip":
        project.trim(index, float(arguments.get("start", project.clips[index].start)),
                     float(arguments.get("end", project.clips[index].end)))
    elif tool == "remove_clip":
        project.delete(index)
    elif tool == "move_clip_with_order_guard":
        old, new = int(arguments["from_index"]), int(arguments["to_index"])
        candidate = list(project.clips); candidate.insert(new, candidate.pop(old))
        body = candidate[1:] if candidate and getattr(candidate[0], "role", "") == "hook" else candidate
        keys = [capture_key(c.path, c.start) for c in body]
        if capture_order_locked and any(a > b for a, b in zip(keys, keys[1:]) if a[0] and b[0]):
            raise ValueError("该移动会破坏钩子后的真实拍摄顺序")
        project.move(old, new)
    elif tool == "set_caption":
        text = str(arguments.get("text") or "")
        if len(text) > 120: raise ValueError("单段字幕最多 120 个字符")
        project.clips[index].caption = text
    elif tool == "set_transition":
        kind = str(arguments.get("transition") or "none")
        if kind not in ALLOWED_TRANSITIONS: raise ValueError("不支持的转场")
        project.clips[index].transition = kind
        project.clips[index].transition_duration = max(.05, min(1.5, float(arguments.get("duration", .35))))
    elif tool == "set_mask":
        shape = str(arguments.get("shape") or "none")
        if shape not in {"none", "spotlight", "ellipse", "diamond", "vertical_strip", "split_left", "split_right", "privacy_blur", "vignette", "portrait_card", "cinema"}: raise ValueError("不支持的蒙版")
        project.clips[index].mask_shape = shape
    elif tool == "set_volume":
        project.clips[index].volume = max(0, min(2, float(arguments.get("volume", 1))))
    after = copy.deepcopy(project.to_dict())
    return {"transaction_id": str(uuid.uuid4()), "tool": tool, "arguments": dict(arguments),
            "before": before, "after": after}
