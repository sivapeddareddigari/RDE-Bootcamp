from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 900
BACKGROUND = (255, 255, 255)
LINE_COLOR = (0, 0, 0)
BOX_FILL = (255, 255, 255)
TEXT_COLOR = (0, 0, 0)
FONT_SIZE_TITLE = 36
FONT_SIZE_LABEL = 24
FONT_SIZE_BODY = 20

img = Image.new('RGB', (WIDTH, HEIGHT), BACKGROUND)
draw = ImageDraw.Draw(img)

try:
    title_font = ImageFont.truetype('DejaVuSans-Bold.ttf', FONT_SIZE_TITLE)
    label_font = ImageFont.truetype('DejaVuSans-Bold.ttf', FONT_SIZE_LABEL)
    body_font = ImageFont.truetype('DejaVuSans.ttf', FONT_SIZE_BODY)
except IOError:
    title_font = ImageFont.load_default()
    label_font = ImageFont.load_default()
    body_font = ImageFont.load_default()

nodes = {
    'trigger': {'xy': (120, 40), 'size': (300, 100), 'title': 'Submission Trigger', 'lines': ['Employee reimbursement request']},
    'load': {'xy': (120, 170), 'size': (300, 170), 'title': 'Load Inputs', 'lines': ['SAP', 'Docs', 'Emails']},
    'normalize': {'xy': (460, 170), 'size': (320, 170), 'title': 'Normalize & Link', 'lines': ['IDs', 'Currency']},
    'match': {'xy': (860, 170), 'size': (320, 170), 'title': 'Match & Reconcile', 'lines': ['Receipts', 'Timecards']},
    'rule': {'xy': (1220, 170), 'size': (300, 170), 'title': 'Rule Checks', 'lines': ['Policy', 'Eligibility']},
    'flag': {'xy': (460, 410), 'size': (320, 170), 'title': 'Flag Exceptions', 'lines': ['Missing backup', 'Holds']},
    'decide': {'xy': (760, 410), 'size': (320, 170), 'title': 'Apply Decisions', 'lines': ['Bill / Adjust', 'Exclude / Escalate']},
    'kpi': {'xy': (1220, 410), 'size': (300, 170), 'title': 'KPI Validation', 'lines': ['Accuracy', 'Approval rate']},
    'output': {'xy': (420, 670), 'size': (380, 160), 'title': 'Generate Outputs', 'lines': ['Review summary', 'KPI dashboard', 'Audit trail']},
    'notify': {'xy': (920, 670), 'size': (380, 160), 'title': 'Notify Stakeholders', 'lines': ['Employee approval/correct', 'Analyst escalation']},
}

for node in nodes.values():
    x, y = node['xy']
    w, h = node['size']
    draw.rectangle([x, y, x + w, y + h], outline=LINE_COLOR, width=4, fill=BOX_FILL)
    title_w, title_h = draw.textsize(node['title'], font=label_font)
    draw.text((x + (w - title_w) / 2, y + 16), node['title'], fill=TEXT_COLOR, font=label_font)
    text_y = y + 54
    for line in node['lines']:
        draw.text((x + 20, text_y), f'- {line}', fill=TEXT_COLOR, font=body_font)
        text_y += 34


def anchor_top(node):
    x, y = node['xy']
    w, _ = node['size']
    return x + w / 2, y


def anchor_bottom(node):
    x, y = node['xy']
    w, h = node['size']
    return x + w / 2, y + h


def anchor_left(node):
    x, y = node['xy']
    _, h = node['size']
    return x, y + h / 2


def anchor_right(node):
    x, y = node['xy']
    w, h = node['size']
    return x + w, y + h / 2


def output_entry_left(node):
    x, y = node['xy']
    return x + 140, y - 10


def output_entry_right(node):
    x, y = node['xy']
    w, _ = node['size']
    return x + w - 140, y - 10


def anchor_middle_right(node):
    x, y = node['xy']
    w, h = node['size']
    return x + w, y + h / 2

arrow_paths = [
    [anchor_bottom(nodes['trigger']), anchor_top(nodes['load'])],
    [anchor_right(nodes['load']), anchor_left(nodes['normalize'])],
    [anchor_right(nodes['normalize']), anchor_left(nodes['match'])],
    [anchor_right(nodes['match']), anchor_left(nodes['rule'])],
    [anchor_bottom(nodes['normalize']), anchor_top(nodes['flag'])],
    [anchor_right(nodes['flag']), anchor_left(nodes['decide'])],
    [
        anchor_bottom(nodes['match']),
        (anchor_bottom(nodes['match'])[0], anchor_bottom(nodes['match'])[1] + 40),
        (anchor_left(nodes['decide'])[0] - 20, anchor_bottom(nodes['match'])[1] + 40),
        (anchor_left(nodes['decide'])[0] - 20, anchor_top(nodes['decide'])[1] + 20),
        anchor_left(nodes['decide'])
    ],
    [anchor_bottom(nodes['rule']), anchor_top(nodes['kpi'])],
    [
        anchor_bottom(nodes['decide']),
        (anchor_bottom(nodes['decide'])[0], nodes['output']['xy'][1] - 30),
        (output_entry_left(nodes['output'])[0], nodes['output']['xy'][1] - 30),
        output_entry_left(nodes['output'])
    ],
    [
        anchor_bottom(nodes['kpi']),
        (anchor_bottom(nodes['kpi'])[0], nodes['output']['xy'][1] - 30),
        (output_entry_right(nodes['output'])[0], nodes['output']['xy'][1] - 30),
        output_entry_right(nodes['output'])
    ],
    [
        anchor_right(nodes['output']),
        (anchor_right(nodes['output'])[0] + 60, anchor_right(nodes['output'])[1]),
        anchor_left(nodes['notify'])
    ],
]

arrow_size = 16


def draw_arrow(path):
    draw.line(path, fill=LINE_COLOR, width=4)
    x1, y1 = path[-1]
    x0, y0 = path[-2]
    dx = x1 - x0
    dy = y1 - y0
    if abs(dx) > abs(dy):
        sign = 1 if dx > 0 else -1
        arrow = [(x1, y1), (x1 - sign * arrow_size, y1 - 8), (x1 - sign * arrow_size, y1 + 8)]
    else:
        sign = 1 if dy > 0 else -1
        arrow = [(x1, y1), (x1 - 8, y1 - sign * arrow_size), (x1 + 8, y1 - sign * arrow_size)]
    draw.polygon(arrow, fill=LINE_COLOR)

for path in arrow_paths:
    draw_arrow(path)

header = 'Review Workflow with KPI Validation'
h_w, h_h = draw.textsize(header, font=title_font)
draw.text(((WIDTH - h_w) / 2, 40), header, fill=TEXT_COLOR, font=title_font)

img.save('review_workflow.png')
print('Generated review_workflow.png')
