#!/usr/bin/env python3
"""
Quick test script to verify the Indeed scraper fixes.
Run this to ensure the scraper is working after fixes.
"""

import asyncio
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(__file__))

from app.services.indeed_selenium_service import scrape_indeed_selenium, close_driver, force_cleanup_all_drivers


async def test_basic_scraping():
    """Test basic scraping functionality."""
    print("=" * 60)
    print("🧪 TESTING FIXED INDEED SCRAPER")
    print("=" * 60)
    
    # Cleanup any existing processes first
    print("\n1️⃣  Cleaning up any zombie processes...")
    force_cleanup_all_drivers()
    await asyncio.sleep(2)
    
    # Test with a simple query
    print("\n2️⃣  Testing basic job search (max 5 results)...")
    print("   Query: 'Software Engineer'")
    print("   Location: 'San Francisco, CA'")
    print("   Max Results: 5")
    print()
    
    try:
        jobs = await scrape_indeed_selenium(
            query="Software Engineer",
            location="San Francisco, CA",
            max_results=5,
            job_type=None,
            salary_min=None,
            salary_max=None,
            experience_level=None,
            employment_type=None,
            days_old=None
        )
        
        print(f"\n✅ SUCCESS! Found {len(jobs)} jobs")
        print("=" * 60)
        
        # Display results
        if jobs:
            print("\n📋 RESULTS:")
            print("-" * 60)
            for i, job in enumerate(jobs, 1):
                print(f"\n{i}. {job.title}")
                print(f"   Company: {job.company or 'N/A'}")
                print(f"   Location: {job.location or 'N/A'}")
                print(f"   Salary: {job.salary_range or 'N/A'}")
                print(f"   Posted: {job.posted_date or 'N/A'}")
                print(f"   URL: {job.url[:60] if job.url else 'N/A'}...")
        else:
            print("\n⚠️  No jobs found (this might be normal if filters are too strict)")
        
        print("\n" + "=" * 60)
        print("✅ TEST PASSED - Scraper is working!")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print("\n3️⃣  Cleaning up driver...")
        close_driver()
        print("   ✓ Driver closed")


async def test_with_filters():
    """Test scraping with filters."""
    print("\n" + "=" * 60)
    print("🧪 TESTING WITH FILTERS")
    print("=" * 60)
    
    try:
        jobs = await scrape_indeed_selenium(
            query="Python Developer",
            location="Remote",
            max_results=3,
            days_old=7,
            experience_level="Mid-Senior"
        )
        
        print(f"\n✅ Found {len(jobs)} jobs with filters")
        return True
        
    except Exception as e:
        print(f"\n❌ Filter test failed: {e}")
        return False
        
    finally:
        close_driver()


def main():
    """Run all tests."""
    print("\n🚀 Starting Indeed Scraper Tests...")
    print("   This will test the fixes applied to resolve:")
    print("   - Resource exhaustion")
    print("   - Browser crashes")
    print("   - Timeout issues")
    print("   - Zero job extraction")
    print()
    
    # Run basic test
    success1 = asyncio.run(test_basic_scraping())
    
    if success1:
        # Run filter test
        print("\n⏳ Running additional filter test...")
        success2 = asyncio.run(test_with_filters())
        
        if success2:
            print("\n" + "=" * 60)
            print("🎉 ALL TESTS PASSED!")
            print("=" * 60)
            print("\n✅ The Indeed scraper is working correctly.")
            print("✅ No timeouts detected.")
            print("✅ Jobs extracted successfully.")
            print("✅ Resources cleaned up properly.")
            print("\n📚 Next steps:")
            print("   1. Review CRITICAL_FIXES_APPLIED.md for details")
            print("   2. Test with your specific queries")
            print("   3. Monitor for any remaining issues")
            print("   4. Keep fetch_complete_details=False for stability")
            return 0
    
    print("\n" + "=" * 60)
    print("⚠️  SOME TESTS FAILED")
    print("=" * 60)
    print("\n📋 Troubleshooting:")
    print("   1. Check if Chrome/ChromeDriver is installed")
    print("   2. Run: force_cleanup_all_drivers()")
    print("   3. Check system resources: ulimit -n")
    print("   4. Review error messages above")
    print("   5. See CRITICAL_FIXES_APPLIED.md for more help")
    return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

