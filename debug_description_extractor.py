"""
Debug version of the description extractor with extensive logging.
Use this to see what's actually on the Indeed page.
"""

import re
from typing import Optional
from bs4 import BeautifulSoup


def _clean_and_format_description(text: str) -> str:
    """Clean and format job description text."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove multiple newlines (keep max 2)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    # Trim
    text = text.strip()
    return text


def _extract_description_from_full_page_improved_debug(soup: BeautifulSoup) -> Optional[str]:
    """
    DEBUG VERSION: Extract description with extensive logging.
    """
    
    print("\n" + "="*80)
    print("DEBUG: Starting description extraction")
    print("="*80)
    
    # Get page text for analysis
    page_text = soup.get_text()
    print(f"Total page text length: {len(page_text)} chars")
    print(f"First 500 chars: {page_text[:500]}")
    print("-"*80)
    
    # Check for Cloudflare/blocking
    cloudflare_indicators = [
        "Checking your browser",
        "Enable JavaScript and cookies",
        "challenge-platform",
        "cf-browser-verification",
        "Just a moment",
        "Ray ID",
        "Cloudflare",
        "Access denied",
        "cf-challenge"
    ]
    
    for indicator in cloudflare_indicators:
        if indicator in page_text:
            print(f"⚠️  WARNING: Found Cloudflare indicator: '{indicator}'")
            print(f"   Page may be blocked!")
    
    print("-"*80)
    
    # Strategy 1: Look for "Full job description" heading
    print("\n[Strategy 1] Searching for 'Full job description' heading...")
    full_job_desc_heading = soup.find(string=re.compile(r'Full job description', re.IGNORECASE))
    if full_job_desc_heading:
        print(f"  ✓ Found 'Full job description' heading")
        print(f"  Heading text: '{full_job_desc_heading}'")
        parent = full_job_desc_heading.find_parent()
        if parent:
            print(f"  Parent tag: <{parent.name}>")
            print(f"  Parent classes: {parent.get('class', [])}")
            
            # Try next sibling
            next_section = parent.find_next_sibling()
            if next_section:
                print(f"  Next sibling tag: <{next_section.name}>")
                print(f"  Next sibling classes: {next_section.get('class', [])}")
                text = next_section.get_text(separator='\n', strip=True)
                print(f"  Next sibling text length: {len(text)} chars")
                print(f"  Next sibling preview: {text[:200]}")
                if len(text) > 100:
                    text = _clean_and_format_description(text)
                    print(f"  ✓✓ SUCCESS: Extracted {len(text)} chars from next sibling")
                    return text
            
            # Try multiple siblings
            print("  Trying to collect from multiple siblings...")
            remaining_text = []
            current = parent.find_next_sibling()
            sibling_count = 0
            while current and sibling_count < 10:
                text_content = current.get_text(separator='\n', strip=True)
                if text_content and len(text_content) > 20:
                    print(f"    Sibling {sibling_count + 1}: {len(text_content)} chars")
                    remaining_text.append(text_content)
                current = current.find_next_sibling()
                sibling_count += 1
            
            if remaining_text:
                combined = '\n\n'.join(remaining_text)
                combined = _clean_and_format_description(combined)
                print(f"  ✓✓ SUCCESS: Extracted {len(combined)} chars from {len(remaining_text)} siblings")
                return combined
    else:
        print("  ✗ 'Full job description' heading not found")
    
    # Strategy 2: Look for "Company Description" heading
    print("\n[Strategy 2] Searching for 'Company Description' heading...")
    company_desc_heading = soup.find(string=re.compile(r'Company Description', re.IGNORECASE))
    if company_desc_heading:
        print(f"  ✓ Found 'Company Description' heading")
        parent = company_desc_heading.find_parent()
        if parent:
            text_parts = [parent.get_text(separator='\n', strip=True)]
            next_elem = parent.find_next_sibling()
            count = 0
            while next_elem and count < 5:
                text_content = next_elem.get_text(separator='\n', strip=True)
                if text_content:
                    text_parts.append(text_content)
                next_elem = next_elem.find_next_sibling()
                count += 1
            
            combined = '\n\n'.join(text_parts)
            combined = _clean_and_format_description(combined)
            if len(combined) > 100:
                print(f"  ✓✓ SUCCESS: Extracted {len(combined)} chars from Company Description")
                return combined
    else:
        print("  ✗ 'Company Description' heading not found")
    
    # Strategy 3: CSS selectors
    print("\n[Strategy 3] Trying CSS selectors...")
    desc_selectors = [
        'div.jobsearch-jobDescriptionText',
        'div[class*="jobsearch-jobDescriptionText"]',
        'div#jobDescriptionText',
        'div[id*="jobDescriptionText"]',
        'div[data-testid="job-description"]',
        'div[data-testid="jobsearch-JobComponent-description"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="job-description"]',
        'div[class*="jobDescription"]',
    ]
    
    for selector in desc_selectors:
        elements = soup.select(selector)
        if elements:
            print(f"  ✓ Found {len(elements)} elements with selector: {selector}")
            for i, elem in enumerate(elements):
                text = elem.get_text(separator='\n', strip=True)
                print(f"    Element {i+1}: {len(text)} chars")
                print(f"    Preview: {text[:200]}")
                if len(text) > 100:
                    text = _clean_and_format_description(text)
                    print(f"  ✓✓ SUCCESS: Extracted {len(text)} chars using selector")
                    return text
        else:
            print(f"  ✗ No elements found for: {selector}")
    
    # Strategy 4: Look for divs with "job description" text
    print("\n[Strategy 4] Searching for divs containing 'job description'...")
    all_divs = soup.find_all('div')
    print(f"  Total divs on page: {len(all_divs)}")
    candidates = 0
    for div in all_divs:
        if len(div.find_all('div', recursive=False)) > 5:
            continue
        
        text = div.get_text(separator='\n', strip=True)
        if (len(text) > 300 and  
            len(text.split()) > 50 and
            'job description' in text.lower()[:200]):
            candidates += 1
            print(f"  Candidate {candidates}: {len(text)} chars")
            print(f"  Preview: {text[:200]}")
            text = _clean_and_format_description(text)
            print(f"  ✓✓ SUCCESS: Found description in generic div")
            return text
    print(f"  ✗ No suitable divs found (checked {candidates} candidates)")
    
    # Strategy 5: Main content area
    print("\n[Strategy 5] Searching main content area...")
    main_selectors = ['main', 'article', 'div[role="main"]']
    for selector in main_selectors:
        elem = soup.select_one(selector)
        if elem:
            print(f"  ✓ Found {selector}")
            text_blocks = []
            for child in elem.find_all(['div', 'section', 'article']):
                text = child.get_text(separator='\n', strip=True)
                if len(text) > 300 and len(text.split()) > 50:
                    text_blocks.append((len(text), text))
            
            print(f"  Found {len(text_blocks)} text blocks")
            if text_blocks:
                text_blocks.sort(reverse=True)
                text = _clean_and_format_description(text_blocks[0][1])
                print(f"  ✓✓ SUCCESS: Extracted {len(text)} chars from main area")
                return text
        else:
            print(f"  ✗ No {selector} found")
    
    # Strategy 6: Paragraph clustering
    print("\n[Strategy 6] Trying paragraph clustering...")
    paragraphs = soup.find_all('p')
    print(f"  Found {len(paragraphs)} paragraphs")
    if len(paragraphs) > 3:
        substantial_p = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50]
        print(f"  Found {len(substantial_p)} substantial paragraphs")
        if substantial_p:
            combined_text = '\n\n'.join(substantial_p)
            combined_text = _clean_and_format_description(combined_text)
            if len(combined_text) > 300:
                print(f"  ✓✓ SUCCESS: Extracted {len(combined_text)} chars from paragraphs")
                return combined_text
    
    print("\n" + "="*80)
    print("❌ ALL STRATEGIES FAILED - No description found")
    print("="*80)
    
    # Final debug: Show all headings on page
    print("\n[DEBUG] All headings on page:")
    for tag in ['h1', 'h2', 'h3', 'h4']:
        headings = soup.find_all(tag)
        if headings:
            print(f"  {tag}: {[h.get_text(strip=True)[:100] for h in headings[:5]]}")
    
    # Show all text content longer than 200 chars
    print("\n[DEBUG] All text blocks > 200 chars:")
    all_elements = soup.find_all(['div', 'section', 'article', 'p'])
    long_texts = []
    for elem in all_elements:
        text = elem.get_text(strip=True)
        if len(text) > 200 and len(text.split()) > 30:
            long_texts.append((len(text), text[:200]))
    
    long_texts.sort(reverse=True)
    for i, (length, preview) in enumerate(long_texts[:5]):
        print(f"  Block {i+1}: {length} chars - {preview}")
    
    return None


# Usage example
if __name__ == "__main__":
    # Test with minimal HTML
    test_html = """
    <html><body>
        <h1>Job Title</h1>
        <h2>Full job description</h2>
        <div class="content">
            <p>This is a test job description with enough content to be extracted.</p>
            <p>It has multiple paragraphs to simulate a real job posting.</p>
        </div>
    </body></html>
    """
    
    soup = BeautifulSoup(test_html, 'html.parser')
    result = _extract_description_from_full_page_improved_debug(soup)
    print(f"\n\nFinal result: {result}")