"""One expression board, not eight separate portraits.

Kolors has no usable identity lock in this repo: image-to-image is skipped
because a portrait reference plus a scene prompt melts the person into the
background. Separate text-to-image calls drift the face. A single six-cell
board keeps one seed, one outfit, and one framing, which is what a Remotion
stage needs in order to swap faces without the body jumping.

Cell order, left to right, top to bottom:
neutral, talk, smile, angry, sad, shock.
"""

CELL_KEYS = ("neutral", "talk", "smile", "angry", "sad", "shock")

BOARD_PROMPT = (
    "二次元角色表情设定图，同一个人，六宫格，两行三列，格子大小完全相等，"
    "格子之间是细白分隔线。每个格子都是同一个年轻男人的胸部以上正面半身，"
    "构图、姿势、服装、光线完全一致，面向镜头，居中。"
    "短而凌乱的黑发，疲惫的深棕色眼睛，灰色拉链连帽衫，白色T恤。"
    "每个格子内部是纯亮绿色抠像背景。不要文字，不要编号，不要水印，不要手。"
    "从左到右、从上到下：平静闭嘴；嘴巴微张正在说话；温和微笑且闭嘴；"
    "生气皱眉；难过垂眼；惊讶睁大眼睛并张大嘴。"
    "平涂赛璐璐，干净线稿，只有这一个角色。"
)

BOARD_NEGATIVE = (
    "text, letters, numbers, caption, watermark, logo, signature, extra person, "
    "hands, full body, scenery, photorealistic, 3d render, blurry, "
    "deformed face, extra eyes, extra mouth"
)

ROOM_PROMPT = (
    "空的夜晚中式小房间，无人，没有人影，没有角色。"
    "左边木书桌和一盏暖色台灯，一本合上的笔记本，右边窗户有雨和远处城市灯光，"
    "一把简单的椅子。二次元动画背景，新海诚配色，横构图，不要文字，不要水印。"
)

ROOM_NEGATIVE = "person, people, face, silhouette, character, text, watermark, logo"
