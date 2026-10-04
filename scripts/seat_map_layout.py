from __future__ import annotations


# Coordinates are measured against /Users/oscar/wedding-system/wedding_seats.jpg
# at 1721 x 2435. seat_video_key identifies a physical position and stays stable
# when a table name changes.
TABLES = [
    {"seat_video_key": "table-01", "table_number": 1, "table_name": "女方家人A", "x": 365, "y": 606, "radius": 92},
    {"seat_video_key": "table-02", "table_number": 2, "table_name": "男方家人A", "x": 1354, "y": 606, "radius": 92},
    {"seat_video_key": "table-03", "table_number": 3, "table_name": "女方親友A", "x": 365, "y": 840, "radius": 92},
    {"seat_video_key": "table-04", "table_number": 4, "table_name": "女方家人B", "x": 660, "y": 876, "radius": 92},
    {"seat_video_key": "table-05", "table_number": 5, "table_name": "男方家人C", "x": 1055, "y": 876, "radius": 92},
    {"seat_video_key": "table-06", "table_number": 6, "table_name": "男方家人B", "x": 1354, "y": 840, "radius": 92},
    {"seat_video_key": "table-07", "table_number": 7, "table_name": "新娘朋友A", "x": 365, "y": 1074, "radius": 92},
    {"seat_video_key": "table-08", "table_number": 8, "table_name": "女方家人C", "x": 660, "y": 1113, "radius": 92},
    {"seat_video_key": "table-09", "table_number": 9, "table_name": "男方家人E", "x": 1055, "y": 1092, "radius": 92},
    {"seat_video_key": "table-10", "table_number": 10, "table_name": "男方家人D", "x": 1354, "y": 1074, "radius": 92},
    {"seat_video_key": "table-11", "table_number": 11, "table_name": "新娘同事A", "x": 365, "y": 1308, "radius": 92},
    {"seat_video_key": "table-12", "table_number": 12, "table_name": "新娘國中同學A", "label_lines": ["新娘國中", "同學A"], "x": 660, "y": 1350, "radius": 92},
    {"seat_video_key": "table-13", "table_number": 13, "table_name": "新郎同事A", "x": 1055, "y": 1305, "radius": 92},
    {"seat_video_key": "table-14", "table_number": 14, "table_name": "男方長輩好友", "label_lines": ["男方長輩", "好友"], "x": 1354, "y": 1308, "radius": 92},
    {"seat_video_key": "table-15", "table_number": 15, "table_name": "新娘同事B", "x": 365, "y": 1542, "radius": 92},
    {"seat_video_key": "table-16", "table_number": 16, "table_name": "新娘國中同學B", "label_lines": ["新娘國中", "同學B"], "x": 660, "y": 1587, "radius": 92},
    {"seat_video_key": "table-17", "table_number": 17, "table_name": "新郎同事B", "x": 1055, "y": 1521, "radius": 92},
    {"seat_video_key": "table-18", "table_number": 18, "table_name": "新郎好友A", "x": 1354, "y": 1542, "radius": 92},
    {"seat_video_key": "table-19", "table_number": 19, "table_name": "新郎研究所同學", "x": 365, "y": 1776, "radius": 92},
    {"seat_video_key": "table-20", "table_number": 20, "table_name": "新娘大學同學", "label_lines": ["新娘大學", "同學"], "x": 660, "y": 1824, "radius": 92},
    {"seat_video_key": "table-21", "table_number": 21, "table_name": "新郎同事C", "x": 1055, "y": 1734, "radius": 92},
    {"seat_video_key": "table-22", "table_number": 22, "table_name": "新郎好友B", "x": 1354, "y": 1776, "radius": 92},
    {"seat_video_key": "table-23", "table_number": 23, "table_name": "預備桌", "x": 365, "y": 2010, "radius": 92},
    {"seat_video_key": "table-24", "table_number": 24, "table_name": "新娘朋友B", "x": 669, "y": 2061, "radius": 92},
    {"seat_video_key": "table-25", "table_number": 25, "table_name": "新郎同事E", "x": 1064, "y": 1947, "radius": 92},
    {"seat_video_key": "table-26", "table_number": 26, "table_name": "新郎同事D", "x": 1354, "y": 2010, "radius": 92},
]

MAIN_TABLE = {
    "seat_video_key": "main-table",
    "table_number": None,
    "table_name": "主桌",
    "x": 864,
    "y": 645,
    "radius": 116,
    "main": True,
}
