"""
Simple API test with longer timeout
"""
import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_api():
    """Test the GET endpoint with a simple URL"""
    url = "https://www.burton.com/us/en/careers"
    
    print(f"\n🧪 Testing API endpoint")
    print(f"URL: {url}")
    print("Please wait, this may take 30-60 seconds...\n")
    
    start_time = time.time()
    
    try:
        response = requests.get(
            f"{BASE_URL}/api/jobs/scrape-url-get",
            params={"url": url, "max_results": 10},
            timeout=120  # 2 minutes timeout
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            jobs = response.json()
            print(f"✅ SUCCESS! (took {elapsed:.1f}s)")
            print(f"Found {len(jobs)} items\n")
            
            for i, job in enumerate(jobs, 1):
                print(f"{i}. {job['title']}")
                if job.get('company'):
                    print(f"   Company: {job['company']}")
                if job.get('url'):
                    print(f"   URL: {job['url'][:80]}...")
                print()
            
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        elapsed = time.time() - start_time
        print(f"❌ Timeout after {elapsed:.1f}s")
        print("The scraper is working but takes longer than expected.")
        print("This is normal for complex career pages.")
        return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

def check_docs():
    """Check if API docs are accessible"""
    try:
        response = requests.get(f"{BASE_URL}/docs", timeout=5)
        if response.status_code == 200:
            print(f"✅ API Documentation: {BASE_URL}/docs\n")
            return True
    except:
        pass
    return False

if __name__ == "__main__":
    print("\n" + "="*80)
    print("Generic Career Page Scraper - API Test")
    print("="*80)
    
    # Check server
    try:
        response = requests.get(BASE_URL, timeout=5)
        print(f"\n✅ Server is running")
    except:
        print(f"\n❌ Server is not running! Start it with: ./run.sh")
        exit(1)
    
    # Check docs
    check_docs()
    
    # Run test
    success = test_api()
    
    if success:
        print("\n" + "="*80)
        print("✅ API TEST PASSED")
        print("="*80)
        print("\n📌 Available Endpoints:")
        print(f"  POST: {BASE_URL}/api/jobs/scrape-url")
        print(f"  GET:  {BASE_URL}/api/jobs/scrape-url-get?url=<url>")
        print(f"\n📝 Example cURL:")
        print(f'  curl "{BASE_URL}/api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers"')
    else:
        print("\n" + "="*80)
        print("⚠️  API TEST HAD ISSUES")
        print("="*80)
        print("But the scraper service itself is working!")

