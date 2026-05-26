from pydantic import BaseModel, Field
from typing import Optional, List


class Job(BaseModel):
    title: str
    company: Optional[str] = None
    company_url: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    salary_range: Optional[str] = None
    job_type: Optional[str] = None  # Full-time, Part-time, Contract, etc.
    posted_date: Optional[str] = None
    experience_level: Optional[str] = None  # Entry, Mid, Senior, etc.
    benefits: Optional[List[str]] = None
    requirements: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    remote_type: Optional[str] = None  # Remote, Hybrid, On-site
    employment_type: Optional[str] = None  # Full-time, Part-time, Contract, Internship
    industry: Optional[str] = None
    company_size: Optional[str] = None
    job_id: Optional[str] = None

    # Indeed-specific high-value fields. All optional so other scrapers stay
    # backward-compatible — they simply leave these unset.
    job_key: Optional[str] = None  # Indeed's stable vjk; primary dedup key
    apply_url: Optional[str] = None  # Direct apply link (vs SERP /viewjob redirect)
    easy_apply: Optional[bool] = None  # Indeed Easy Apply available?
    sponsored: Optional[bool] = None  # Paid placement marker
    company_rating: Optional[float] = None  # 1.0–5.0
    review_count: Optional[int] = None
    posted_date_iso: Optional[str] = None  # ISO date derived from "3 days ago"
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None  # USD/EUR/GBP — defaults USD on indeed.com
    salary_period: Optional[str] = None  # "year" | "month" | "week" | "day" | "hour"
