"""
Test script for the generic career page scraper
"""
import asyncio
import json
from app.services.generic_career_scraper import scrape_generic_career_page

# Test URLs provided by the user
TEST_URLS = [
    "https://www.burton.com/us/en/careers",
    "https://skida.com/pages/careers",
    "https://thujasocks.com/pages/careers",
    "https://darntough.com/pages/careers",
    "https://www.turtlefur.com/pages/careers",
    "https://vermontglove.com/pages/careers",
    "https://orvis.com/pages/careers",
    "https://www.concept2.com/company/employment",
]


async def test_single_url(url: str):
    """Test scraping a single URL"""
    print(f"\n{'=' * 80}")
    print(f"Testing URL: {url}")
    print('=' * 80)
    
    try:
        jobs = await scrape_generic_career_page(url, max_results=10)
        
        print(f"\n✅ Successfully scraped {len(jobs)} jobs from {url}")
        
        if jobs:
            print("\n📋 Sample jobs:")
            for i, job in enumerate(jobs[:3], 1):  # Show first 3 jobs
                print(f"\n{i}. {job.title}")
                print(f"   Company: {job.company}")
                print(f"   Location: {job.location or 'N/A'}")
                print(f"   Type: {job.remote_type or 'N/A'}")
                print(f"   Employment: {job.employment_type or 'N/A'}")
                print(f"   URL: {job.url or 'N/A'}")
                if job.description:
                    desc = job.description[:100] + "..." if len(job.description) > 100 else job.description
                    print(f"   Description: {desc}")
        else:
            print("⚠️  No jobs found on this page")
            
        return {"url": url, "success": True, "count": len(jobs)}
        
    except Exception as e:
        print(f"\n❌ Error scraping {url}: {str(e)}")
        return {"url": url, "success": False, "error": str(e)}


async def test_all_urls():
    """Test all URLs sequentially"""
    results = []
    
    print("=" * 80)
    print("Starting Generic Career Page Scraper Tests")
    print("=" * 80)
    
    for url in TEST_URLS:
        result = await test_single_url(url)
        results.append(result)
        
        # Add a delay between requests to be respectful
        await asyncio.sleep(2)
    
    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    successful = [r for r in results if r.get('success')]
    failed = [r for r in results if not r.get('success')]
    
    print(f"\n✅ Successful: {len(successful)}/{len(results)}")
    print(f"❌ Failed: {len(failed)}/{len(results)}")
    
    if successful:
        print("\n📊 Jobs found per site:")
        for result in successful:
            print(f"   - {result['url']}: {result['count']} jobs")
    
    if failed:
        print("\n❌ Failed sites:")
        for result in failed:
            print(f"   - {result['url']}: {result.get('error', 'Unknown error')}")
    
    return results


if __name__ == "__main__":
    print("\n🚀 Generic Career Page Scraper Test\n")
    results = asyncio.run(test_all_urls())
    
    # Save results to JSON
    with open('test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n💾 Results saved to test_results.json")

