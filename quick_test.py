"""
Quick test of the generic scraper with a simple URL
"""
import asyncio
from app.services.generic_career_scraper import scrape_generic_career_page

async def quick_test():
    """Test with a simple URL"""
    # Test with a simpler URL first
    url = "https://darntough.com/pages/careers"
    
    print(f"Testing URL: {url}")
    print("Please wait, this may take 10-30 seconds...\n")
    
    try:
        jobs = await scrape_generic_career_page(url, max_results=5)
        
        print(f"\n✅ SUCCESS! Found {len(jobs)} jobs\n")
        
        for i, job in enumerate(jobs, 1):
            print(f"{i}. {job.title}")
            print(f"   Company: {job.company}")
            print(f"   Location: {job.location or 'N/A'}")
            print(f"   Type: {job.remote_type or 'N/A'}")
            print(f"   URL: {job.url or 'N/A'}")
            print()
            
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(quick_test())

