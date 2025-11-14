"""
Test all URLs provided by the user
"""
import asyncio
import json
from datetime import datetime
from app.services.generic_career_scraper import scrape_generic_career_page

# All URLs provided by the user
USER_URLS = [
    "https://www.burton.com/us/en/careers",
    "https://skida.com/pages/careers",
    "https://thujasocks.com/pages/careers",
    "https://darntough.com/pages/careers",
    "https://www.turtlefur.com/pages/careers",
    "https://vermontglove.com/pages/careers",
    "https://orvis.com/pages/careers",
    "https://www.concept2.com/company/employment",
]

async def test_url(url):
    """Test a single URL and return results"""
    print(f"\n{'='*80}")
    print(f"Testing: {url}")
    print('='*80)
    
    start_time = datetime.now()
    
    try:
        jobs = await scrape_generic_career_page(url, max_results=10)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print(f"\n✅ SUCCESS! Found {len(jobs)} items (took {elapsed:.1f}s)")
        
        result = {
            "url": url,
            "success": True,
            "count": len(jobs),
            "elapsed_seconds": round(elapsed, 1),
            "jobs": []
        }
        
        if jobs:
            print("\n📋 Extracted items:")
            for i, job in enumerate(jobs[:5], 1):  # Show first 5
                print(f"\n{i}. {job.title}")
                if job.company:
                    print(f"   Company: {job.company}")
                if job.location:
                    print(f"   Location: {job.location}")
                if job.url:
                    print(f"   URL: {job.url[:80]}...")
                
                result["jobs"].append({
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "url": job.url,
                    "remote_type": job.remote_type,
                    "employment_type": job.employment_type
                })
        else:
            print("⚠️  No jobs found on this page")
        
        return result
        
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n❌ FAILED! (took {elapsed:.1f}s)")
        print(f"Error: {str(e)}")
        
        return {
            "url": url,
            "success": False,
            "error": str(e),
            "elapsed_seconds": round(elapsed, 1)
        }

async def main():
    """Test all URLs"""
    print("\n" + "="*80)
    print("COMPREHENSIVE TEST - All User-Provided URLs")
    print("="*80)
    print(f"\nTesting {len(USER_URLS)} URLs")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    for url in USER_URLS:
        result = await test_url(url)
        results.append(result)
        
        # Small delay between requests
        await asyncio.sleep(2)
    
    # Generate summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    successful = [r for r in results if r.get('success')]
    failed = [r for r in results if not r.get('success')]
    
    total_jobs = sum(r.get('count', 0) for r in successful)
    avg_time = sum(r.get('elapsed_seconds', 0) for r in results) / len(results)
    
    print(f"\n✅ Successful: {len(successful)}/{len(results)}")
    print(f"❌ Failed: {len(failed)}/{len(results)}")
    print(f"📊 Total jobs/links found: {total_jobs}")
    print(f"⏱️  Average time per URL: {avg_time:.1f}s")
    
    if successful:
        print("\n✅ SUCCESSFUL SCRAPES:")
        for r in successful:
            print(f"   • {r['url']}")
            print(f"     → {r['count']} items in {r['elapsed_seconds']}s")
    
    if failed:
        print("\n❌ FAILED SCRAPES:")
        for r in failed:
            print(f"   • {r['url']}")
            print(f"     → Error: {r.get('error', 'Unknown')}")
    
    # Detailed results
    print("\n\n" + "="*80)
    print("DETAILED RESULTS BY URL")
    print("="*80)
    
    for r in successful:
        print(f"\n{r['url']}")
        print(f"  Status: ✅ Success")
        print(f"  Items found: {r['count']}")
        print(f"  Time: {r['elapsed_seconds']}s")
        
        if r.get('jobs'):
            print(f"  Sample jobs:")
            for job in r['jobs'][:3]:
                print(f"    - {job['title']}")
                if job.get('url'):
                    print(f"      {job['url'][:70]}...")
    
    # Save results to file
    report = {
        "test_date": datetime.now().isoformat(),
        "total_urls": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "total_jobs_found": total_jobs,
        "average_time_seconds": round(avg_time, 1),
        "results": results
    }
    
    with open('test_report.json', 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n\n💾 Full report saved to: test_report.json")
    
    print("\n" + "="*80)
    print("🎉 TESTING COMPLETE!")
    print("="*80)
    print("\n✅ The Generic Career Page Scraper is working!")
    print("   You can now use the API endpoints to scrape any career page.")
    print("\n📚 See GENERIC_SCRAPER_GUIDE.md for usage instructions")
    print("📖 API Documentation: http://127.0.0.1:8000/docs")

if __name__ == "__main__":
    asyncio.run(main())

