from typing import Optional
from app.models.job_model import Job
# filters.py




def matches_location_filter_indeed(job: Job, location_filter: Optional[str]) -> bool:    
    """Check if job matches the location filter for Indeed with comprehensive location support like ZipRecruiter.
        
        Since we're using Indeed's URL-based location filtering, be very permissive here.
        Indeed has already filtered the results, so we only filter out obvious mismatches.
        """
    if not location_filter:
        return True
    
    # Since Indeed's URL filtering has already done the work, be very permissive
    # Only filter out jobs if they're obviously wrong
    # If no job location is available, accept it (Indeed returned it for a reason)
    if not job.location:
        return True
    
    location_filter = location_filter.lower().strip()
    job_location = job.location.lower()
    
    # For remote searches, be flexible - remote jobs are remote anywhere
    if location_filter in ['remote', 'work from home', 'wfh']:
        # Accept jobs that are remote or have remote indicators
        if job.remote_type and 'remote' in job.remote_type.lower():
            return True
        # Also accept if location contains remote
        if 'remote' in job_location:
            return True
        # Be permissive - if Indeed returned it for a remote search, trust it
        return True
    
    # For non-remote searches, always allow remote jobs (they can be done from anywhere)
    if job.remote_type and job.remote_type.lower() == 'remote':
        return True
    
    # Since Indeed has already filtered by location in the URL, trust its filtering
    # We'll just do a very permissive check here to catch any obvious issues
    
    # Be very permissive - if the location filter appears anywhere in the job location, it's a match
    # This handles partial matches, variations in formatting, etc.
    if location_filter in job_location:
        return True
    
    # Also check common location variations without strict mapping
    # This catches cases where Indeed might use slightly different formatting
    location_parts = location_filter.split()
    if any(part in job_location for part in location_parts if len(part) > 2):
        return True
    
    # Since Indeed returned this job for the location search, trust it
    # Only filter out if it's completely unrelated (which is rare since Indeed already filtered)
    return True



def matches_job_type_filter_indeed(job: Job, job_type_filter: Optional[str]) -> bool: 
    """Check if job matches the job type filter for Indeed."""
    if not job_type_filter:
        return True
    
    job_type_filter = job_type_filter.lower().strip()
    job_remote_type = (job.remote_type or '').lower()
    job_title = (job.title or '').lower()
    job_description = (job.description or '').lower()
    
    # Map filter terms to job remote types
    if job_type_filter in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
        return (job_remote_type in ['remote'] or 
                any(keyword in job_title for keyword in ['remote', 'work from home', 'wfh', 'telecommute']) or
                any(keyword in job_description for keyword in ['remote', 'work from home', 'wfh', 'telecommute']))
    
    elif job_type_filter in ['hybrid', 'partially remote', 'part remote', 'flexible']:
        return (job_remote_type in ['hybrid'] or 
                any(keyword in job_title for keyword in ['hybrid', 'partially remote', 'flexible']) or
                any(keyword in job_description for keyword in ['hybrid', 'partially remote', 'flexible']))
    
    elif job_type_filter in ['onsite', 'on-site', 'on site', 'office', 'in-person', 'in person']:
        return (job_remote_type in ['on-site', 'onsite'] or 
                any(keyword in job_title for keyword in ['onsite', 'on-site', 'office', 'in-person']) or
                any(keyword in job_description for keyword in ['onsite', 'on-site', 'office', 'in-person']))
    
    return True
    

def matches_salary_filter_indeed(job: Job, salary_min: Optional[int], salary_max: Optional[int]) -> bool:
    """Check if job matches the salary filter for Indeed."""
    if not salary_min and not salary_max:
        return True
    
    if not job.salary_range:
        return True  # Don't filter out jobs without salary info
    
    # Extract salary numbers from salary range string
    salary_text = job.salary_range.lower()
    
    # Look for salary patterns and extract numbers
    import re
    salary_patterns = [
        r'\$?(\d+(?:,\d{3})*(?:k|k)?)\s*-\s*\$?(\d+(?:,\d{3})*(?:k|k)?)',
        r'\$?(\d+(?:,\d{3})*(?:k|k)?)\s*/\s*(?:year|yr|hour|hr)',
        r'(\d+(?:,\d{3})*(?:k|k))\s*-\s*(\d+(?:,\d{3})*(?:k|k))'
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, salary_text)
        if match:
            try:
                # Extract and convert salary numbers
                min_sal_str = match.group(1).replace(',', '').replace('k', '000')
                max_sal_str = match.group(2).replace(',', '').replace('k', '000')
                
                min_sal = int(min_sal_str)
                max_sal = int(max_sal_str)
                
                # Check if salary range overlaps with filter range
                if salary_min and salary_max:
                    return not (max_sal < salary_min or min_sal > salary_max)
                elif salary_min:
                    return max_sal >= salary_min
                elif salary_max:
                    return min_sal <= salary_max
                    
            except (ValueError, IndexError):
                continue
    
    return True  # If we can't parse salary, don't filter out

def matches_experience_filter_indeed(job: Job, experience_level: Optional[str]) -> bool:
    """Check if job matches the experience level filter for Indeed."""
    if not experience_level:
        return True
    
    # Since we're using Indeed's URL-based filtering with sc parameter,
    # we should trust Indeed's filtering and not apply additional post-search filtering
    # Indeed has already filtered the results correctly based on the URL parameters
    return True

def matches_employment_filter_indeed(job: Job, employment_type: Optional[str]) -> bool:
    """Check if job matches the employment type filter for Indeed."""
    if not employment_type:
        return True
    
    # Since we're now using Indeed's URL-based filtering, be more permissive
    # Only filter out jobs if they explicitly don't match
    if not job.employment_type:
        return True  # Don't filter out jobs without employment type info
    
    employment_type = employment_type.lower().strip()
    job_employment = job.employment_type.lower()
    
    # Map filter terms to employment types (all lowercase for comparison)
    employment_mappings = {
        'full-time': ['full-time', 'full time', 'fulltime'],
        'part-time': ['part-time', 'part time', 'parttime'],
        'contract': ['contract', 'contractor', 'freelance'],
        'internship': ['internship', 'intern', 'trainee', 'co-op', 'coop', 'student'],
        'temporary': ['temporary', 'temp', 'temporary']
    }
    
    if employment_type in employment_mappings:
        # Check if job matches the employment type
        matches = any(emp_type in job_employment for emp_type in employment_mappings[employment_type])
        if matches:
            return True
        
        # For internship searches, also allow jobs that don't explicitly state employment type
        # since Indeed's URL filtering should handle the main filtering
        if employment_type == 'internship':
            return True  # Be permissive for internships
    
    return True

def matches_date_filter_indeed(job: Job, days_old: Optional[int]) -> bool:
    """Check if job matches the date filter for Indeed (posted within last N days)."""
    if not days_old:
        return True
    
    # Since we're now using Indeed's URL-based filtering with fromage parameter,
    # be more permissive with post-search filtering
    if not job.posted_date:
        return True  # Don't filter out jobs without date info
    
    from datetime import datetime, timedelta
    import re
    
    try:
        # Parse the posted date from various formats
        posted_date = job.posted_date.lower().strip()
        current_date = datetime.now()
        
        # Handle different date formats from Indeed
        if 'today' in posted_date:
            job_date = current_date
        elif 'yesterday' in posted_date:
            job_date = current_date - timedelta(days=1)
        elif 'just posted' in posted_date or 'just now' in posted_date:
            job_date = current_date
        else:
            # Extract number of days/hours from text like "3 days ago", "2 hours ago"
            days_match = re.search(r'(\d+)\s+(?:days?|hours?)\s+ago', posted_date)
            if days_match:
                time_value = int(days_match.group(1))
                if 'hour' in posted_date:
                    # Convert hours to days (approximate)
                    job_date = current_date - timedelta(hours=time_value)
                else:
                    job_date = current_date - timedelta(days=time_value)
            else:
                # If we can't parse the date, be permissive since Indeed's URL filtering should handle it
                return True
        
        # Check if job is within the specified number of days
        days_difference = (current_date - job_date).days
        return days_difference <= days_old
        
    except Exception as e:
        print(f"Error parsing date '{job.posted_date}': {e}")
        # If there's an error parsing, be permissive since Indeed's URL filtering should handle it
        return True
