import math


def get_segment_count(duration_ms: int, segment_time: int) -> str:
    total_segments_count = int(math.ceil((duration_ms / 1000) / segment_time))
    return total_segments_count
