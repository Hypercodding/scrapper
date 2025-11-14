"""
Test multiple career page URLs
"""
import asyncio
from app.services.generic_career_scraper import scrape_generic_career_page

async def test_url(url):
    """Test a single URL"""
    print(f"\n{'='*80}")
    print(f"Testing: {url}")
    print('='*80)
    
    try:
        jobs = await scrape_generic_career_page(url, max_results=5)
        
        print(f"\n✅ Found {len(jobs)} potential items")
        
        if jobs:
            for i, job in enumerate(jobs, 1):
                print(f"\n{i}. {job.title}")
                if job.location:
                    print(f"   Location: {job.location}")
                if job.url:
                    print(f"   URL: {job.url[:80]}...")
        else:
            print("❌ No jobs found")
            
        return len(jobs)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return 0

async def main():
    urls = [
        "https://www.concept2.com/company/employment",
        "https://www.burton.com/us/en/careers",
        "https://skida.com/pages/careers",
    ]
    
    total_jobs = 0
    for url in urls:
        count = await test_url(url)
        total_jobs += count
        await asyncio.sleep(2)  # Be nice to servers
    
    print(f"\n{'='*80}")
    print(f"TOTAL: Found {total_jobs} items across {len(urls)} sites")
    print('='*80)

if __name__ == "__main__":
    asyncio.run(main())

