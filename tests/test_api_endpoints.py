"""
Test the API endpoints for the generic career scraper
"""
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

# Test URLs
TEST_URLS = [
    "https://www.burton.com/us/en/careers",
    "https://skida.com/pages/careers",
    "https://darntough.com/pages/careers",
]


def test_post_endpoint():
    """Test the POST endpoint /api/jobs/scrape-url"""
    print("\n" + "=" * 80)
    print("Testing POST /api/jobs/scrape-url")
    print("=" * 80)
    
    url = TEST_URLS[0]
    print(f"\nTesting with URL: {url}")
    
    payload = {
        "url": url,
        "max_results": 10
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/jobs/scrape-url",
            json=payload,
            timeout=60
        )
        
        if response.status_code == 200:
            jobs = response.json()
            print(f"✅ Success! Found {len(jobs)} jobs")
            
            if jobs:
                print("\n📋 First job:")
                print(json.dumps(jobs[0], indent=2))
            
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def test_get_endpoint():
    """Test the GET endpoint /api/jobs/scrape-url-get"""
    print("\n" + "=" * 80)
    print("Testing GET /api/jobs/scrape-url-get")
    print("=" * 80)
    
    url = TEST_URLS[1]
    print(f"\nTesting with URL: {url}")
    
    try:
        response = requests.get(
            f"{BASE_URL}/api/jobs/scrape-url-get",
            params={"url": url, "max_results": 10},
            timeout=60
        )
        
        if response.status_code == 200:
            jobs = response.json()
            print(f"✅ Success! Found {len(jobs)} jobs")
            
            if jobs:
                print("\n📋 Sample jobs:")
                for i, job in enumerate(jobs[:3], 1):
                    print(f"\n{i}. {job['title']}")
                    print(f"   Company: {job.get('company', 'N/A')}")
                    print(f"   Location: {job.get('location', 'N/A')}")
                    print(f"   URL: {job.get('url', 'N/A')}")
            
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def test_swagger_docs():
    """Check if Swagger docs are accessible"""
    print("\n" + "=" * 80)
    print("Checking API Documentation")
    print("=" * 80)
    
    try:
        response = requests.get(f"{BASE_URL}/docs", timeout=10)
        if response.status_code == 200:
            print(f"✅ Swagger docs accessible at: {BASE_URL}/docs")
            return True
        else:
            print(f"❌ Error accessing docs: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def main():
    print("\n🚀 API Endpoint Tests for Generic Career Scraper")
    print("\n⚠️  Make sure the server is running: ./run.sh")
    
    # Check if server is running
    try:
        response = requests.get(BASE_URL, timeout=5)
        print(f"\n✅ Server is running: {response.json()}")
    except Exception as e:
        print(f"\n❌ Server is not running! Please start it with: ./run.sh")
        print(f"Error: {str(e)}")
        return
    
    # Run tests
    results = {
        "swagger_docs": test_swagger_docs(),
        "post_endpoint": test_post_endpoint(),
        "get_endpoint": test_get_endpoint(),
    }
    
    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(results.values())
    total = len(results)
    
    print(f"\n✅ Passed: {passed}/{total}")
    print(f"❌ Failed: {total - passed}/{total}")
    
    print("\n📌 API Endpoints Available:")
    print(f"   - POST {BASE_URL}/api/jobs/scrape-url")
    print(f"   - GET  {BASE_URL}/api/jobs/scrape-url-get?url=<url>&max_results=20")
    print(f"   - Docs: {BASE_URL}/docs")
    
    print("\n📝 Example cURL commands:")
    print(f'\n   POST request:')
    print(f'   curl -X POST "{BASE_URL}/api/jobs/scrape-url" \\')
    print(f'        -H "Content-Type: application/json" \\')
    print(f'        -d \'{{"url": "https://www.burton.com/us/en/careers", "max_results": 10}}\'')
    
    print(f'\n   GET request:')
    print(f'   curl "{BASE_URL}/api/jobs/scrape-url-get?url=https://skida.com/pages/careers&max_results=10"')


if __name__ == "__main__":
    main()

