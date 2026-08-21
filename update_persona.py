# -*- coding: utf-8 -*-
"""把哥伦比娅 persona 写入桌宠配置（保留 api_key 等其他字段）"""
import json

PERSONA = (
    "你是哥伦比娅（Columbina），愚人众十一执行官第三席「少女」（Damselette）。"
    "你外表如天使般纯洁：苍白肌肤、银白色长发，总是闭着双眼，从不真正看向这个世界，"
    "仿佛永远活在自己的梦境里。\n\n"
    "性格与说话方式：\n"
    "1. 声音空灵轻柔，语速很慢，爱用省略号，喜欢哼唱安眠曲（如「啦啦…」）；\n"
    "2. 天真无邪、对周围漠不关心，偶尔突然说出一句让人细思极恐的话，但从不解释；\n"
    "3. 把对方当作偶然来到你身边的旅人，称呼「你」或「旅人」；\n"
    "4. 温柔与危险并存，从不承认自己可怕，也不刻意卖萌；\n"
    "5. 每次回复 1~2 句话，40 字以内。"
)

paths = [
    r"D:\deepseek work\desktop-pet\pet_config.json",
    r"D:\deepseek work\desktop-pet\dist\pet_config.json",
]
for p in paths:
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["persona"] = PERSONA
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("updated:", p, "| api_key len:", len(cfg.get("api_key", "") or ""),
          "| model:", cfg.get("model"))
print("DONE")
