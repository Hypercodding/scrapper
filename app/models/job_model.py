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
