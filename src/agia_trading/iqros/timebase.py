from __future__ import annotations
_INTERVALS_PER_DAY={"1h":24,"2h":12,"4h":6,"6h":4,"8h":3,"12h":2,"1d":1}
def periods_per_year(interval:str, days_per_year:int=365)->int:
    try: return _INTERVALS_PER_DAY[interval]*days_per_year
    except KeyError as e: raise ValueError(f"unsupported interval: {interval}") from e
