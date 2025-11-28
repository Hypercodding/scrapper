"""
Test the enhanced scraper that follows job board links
"""
import asyncio
from app.services.generic_career_scraper import scrape_generic_career_page

async def test_enhanced():
    """Test enhanced scraper with Burton careers page"""
    url = "https://www.burton.com/us/en/careers"
    
    print("=" * 80)
    print("TESTING ENHANCED SCRAPER")
    print("=" * 80)
    print(f"\n🎯 Target: {url}")
    print("\n⭐ NEW FEATURE: Automatically follows job board links to get actual job titles!")
    print("\nPlease wait, this may take 30-45 seconds...\n")
    
    try:
        # Test 1: Get jobs without search
        print("\n" + "=" * 80)
        print("TEST 1: Extract actual job titles (no search)")
        print("=" * 80)
        
        jobs = await scrape_generic_career_page(url, max_results=10)
        
        if jobs:
            print(f"\n✅ SUCCESS! Found {len(jobs)} jobs\n")
            for i, job in enumerate(jobs[:5], 1):
                print(f"{i}. {job.title}")
                if job.location:
                    print(f"   📍 {job.location}")
                if job.url:
                    print(f"   🔗 {job.url[:80]}...")
                print()
        else:
            print("⚠️  No jobs found")
        
        # Test 2: Search for specific jobs
        print("\n" + "=" * 80)
        print("TEST 2: Search for 'designer' jobs")
        print("=" * 80)
        
        designer_jobs = await scrape_generic_career_page(url, max_results=10, search_query="designer")
        
        if designer_jobs:
            print(f"\n✅ Found {len(designer_jobs)} designer jobs\n")
            for i, job in enumerate(designer_jobs, 1):
                print(f"{i}. {job.title}")
                if job.location:
                    print(f"   📍 {job.location}")
                print()
        else:
            print("⚠️  No designer jobs found")
        
        # Test 3: Search for retail jobs
        print("\n" + "=" * 80)
        print("TEST 3: Search for 'retail' jobs")
        print("=" * 80)
        
        retail_jobs = await scrape_generic_career_page(url, max_results=10, search_query="retail")
        
        if retail_jobs:
            print(f"\n✅ Found {len(retail_jobs)} retail jobs\n")
            for i, job in enumerate(retail_jobs, 1):
                print(f"{i}. {job.title}")
                if job.location:
                    print(f"   📍 {job.location}")
                print()
        else:
            print("⚠️  No retail jobs found")
            
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_enhanced())

