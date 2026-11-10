"""Narrow compatibility for ZipRecruiter's serialized integer publication timestamps."""
import copy
import re


def normalize_zip_timestamp(raw):
    if not isinstance(raw,dict):
        return raw
    stamp=((raw.get('status') or {}).get('postedAtUtc') or {})
    value=stamp.get('seconds') if isinstance(stamp,dict) else None
    if not isinstance(value,str) or not re.fullmatch(r'\$n\d+',value):
        return raw
    result=copy.deepcopy(raw)
    result['status']['postedAtUtc']['seconds']=int(value[2:])
    return result


def install_zip_timestamp_compat():
    from jobspy.ziprecruiter import ZipRecruiter
    original=ZipRecruiter._process_job
    if getattr(original,'_dossier_timestamp_compat',False):
        return
    def compatible(self,raw):
        return original(self,normalize_zip_timestamp(raw))
    compatible._dossier_timestamp_compat=True
    ZipRecruiter._process_job=compatible
