import requests
import re
import json
import urllib.parse
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Union


def get_download_links(url: str) -> Dict[str, Union[List[str], Dict]]:
    """
    Download Instagram content using snapsave.app service
    """
    try:
        # Validate URL
        facebook_pattern = r"(?:https?:\/\/(web\.|www\.|m\.)?(facebook|fb)\.(com|watch)\S+)?$"
        instagram_pattern = r"(https|http):\/\/www\.instagram\.com\/(p|reel|tv|stories)"
        
        if not (re.match(facebook_pattern, url) or re.match(instagram_pattern, url, re.IGNORECASE)):
            raise ValueError("Invalid URL")
        
        def decode_data(data: List[str]) -> str:
            part1, part2, part3, part4, part5, part6 = data
            
            def decode_segment(segment: str, base: int, length: int) -> str:
                char_set = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
                base_set = char_set[:base]
                decode_set = char_set[:length]
                
                decoded_value = 0
                for index, char in enumerate(reversed(segment)):
                    if char in base_set:
                        decoded_value += base_set.index(char) * (base ** index)
                
                result = ""
                while decoded_value > 0:
                    result = decode_set[decoded_value % length] + result
                    decoded_value = decoded_value // length
                
                return result or "0"
            
            part6 = ""
            i = 0
            while i < len(part1):
                segment = ""
                while i < len(part1) and part1[i] != part3[part5]:
                    segment += part1[i]
                    i += 1
                i += 1
                
                for j, char in enumerate(part3):
                    segment = segment.replace(char, str(j))
                
                if segment:
                    part6 += chr(int(decode_segment(segment, part5, 10)) - part4)
            
            return urllib.parse.unquote(part6)
        
        def extract_params(data: str) -> List[str]:
            start = data.find('decodeURIComponent(escape(r))}(') + len('decodeURIComponent(escape(r))}(')
            end = data.find('))', start)
            params_str = data[start:end]
            
            params = []
            for item in params_str.split(','):
                params.append(item.strip().strip('"'))
            return params
        
        def extract_download_url(data: str) -> str:
            start = data.find('getElementById("download-section").innerHTML = "') + len('getElementById("download-section").innerHTML = "')
            end = data.find('"; document.getElementById("inputData").remove(); ', start)
            return data[start:end].replace('\\', '')
        
        def get_video_url(data: str) -> str:
            return extract_download_url(decode_data(extract_params(data)))
        
        # Make request to snapsave.app
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://snapsave.app',
            'referer': 'https://snapsave.app/id',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36'
        }
        
        response = requests.post(
            'https://snapsave.app/action.php?lang=id',
            data=f'url={url}',
            headers=headers
        )
        
        data = response.text
        video_page_content = get_video_url(data)
        soup = BeautifulSoup(video_page_content, 'html.parser')
        
        download_links = []
        thumb_divs = soup.find_all('div', class_='download-items__thumb')
        btn_divs = soup.find_all('div', class_='download-items__btn')
        
        for btn_div in btn_divs:
            link = btn_div.find('a')
            if link and link.get('href'):
                download_url = link.get('href')
                if not re.match(r'https?://', download_url):
                    download_url = 'https://snapsave.app' + download_url
                download_links.append(download_url)
        
        if not download_links:
            raise ValueError("No data found")
        
        return {
            'url': download_links,
            'metadata': {
                'url': url
            }
        }
    
    except Exception as error:
        raise Exception(f"Error: {str(error)}")


class InstagramDownloader:
    def __init__(self):
        self.headers = {
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-FB-Friendly-Name': 'PolarisPostActionLoadPostQueryQuery',
            'X-CSRFToken': 'RVDUooU5MYsBbS1CNN3CzVAuEP8oHB52',
            'X-IG-App-ID': '1217981644879628',
            'X-FB-LSD': 'AVqbxe3J_YA',
            'X-ASBD-ID': '129477',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36'
        }
    
    def get_instagram_post_id(self, url: str) -> Optional[str]:
        """Extract post ID from Instagram URL"""
        pattern = r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|tv|stories|reel)/([^/?#&]+).*"
        match = re.match(pattern, url)
        return match.group(1) if match else None
    
    def encode_graphql_request_data(self, shortcode: str) -> str:
        """Encode GraphQL request data for Instagram API"""
        request_data = {
            'av': '0',
            '__d': 'www',
            '__user': '0',
            '__a': '1',
            '__req': '3',
            '__hs': '19624.HYP:instagram_web_pkg.2.1..0.0',
            'dpr': '3',
            '__ccg': 'UNKNOWN',
            '__rev': '1008824440',
            '__s': 'xf44ne:zhh75g:xr51e7',
            '__hsi': '7282217488877343271',
            '__dyn': '7xeUmwlEnwn8K2WnFw9-2i5U4e0yoW3q32360CEbo1nEhw2nVE4W0om78b87C0yE5ufz81s8hwGwQwoEcE7O2l0Fwqo31w9a9x-0z8-U2zxe2GewGwso88cobEaU2eUlwhEe87q7-0iK2S3qazo7u1xwIw8O321LwTwKG1pg661pwr86C1mwraCg',
            '__csr': 'gZ3yFmJkillQvV6ybimnG8AmhqujGbLADgjyEOWz49z9XDlAXBJpC7Wy-vQTSvUGWGh5u8KibG44dBiigrgjDxGjU0150Q0848azk48N09C02IR0go4SaR70r8owyg9pU0V23hwiA0LQczA48S0f-x-27o05NG0fkw',
            '__comet_req': '7',
            'lsd': 'AVqbxe3J_YA',
            'jazoest': '2957',
            '__spin_r': '1008824440',
            '__spin_b': 'trunk',
            '__spin_t': '1695523385',
            'fb_api_caller_class': 'RelayModern',
            'fb_api_req_friendly_name': 'PolarisPostActionLoadPostQueryQuery',
            'variables': json.dumps({
                'shortcode': shortcode,
                'fetch_comment_count': None,
                'fetch_related_profile_media_count': None,
                'parent_comment_count': None,
                'child_comment_count': None,
                'fetch_like_count': None,
                'fetch_tagged_user_count': None,
                'fetch_preview_comment_count': None,
                'has_threaded_comments': False,
                'hoisted_comment_id': None,
                'hoisted_reply_id': None
            }),
            'server_timestamps': 'true',
            'doc_id': '10015901848480474'
        }
        
        return urllib.parse.urlencode(request_data)
    
    def get_post_graphql_data(self, post_id: str, proxy=None) -> Dict:
        """Get post data from Instagram GraphQL API"""
        try:
            encoded_data = self.encode_graphql_request_data(post_id)
            response = requests.post(
                'https://www.instagram.com/api/graphql',
                data=encoded_data,
                headers=self.headers,
                proxies=proxy
            )
            return response.json()
        except Exception as error:
            raise error
    
    def extract_post_info(self, media_data: Dict) -> Dict:
        """Extract post information from media data"""
        try:
            def get_url_from_data(data):
                if 'edge_sidecar_to_children' in data:
                    urls = []
                    for edge in data['edge_sidecar_to_children']['edges']:
                        node = edge['node']
                        url = node.get('video_url') or node.get('display_url')
                        if url:
                            urls.append(url)
                    return urls
                return [data.get('video_url') or data.get('display_url')]
            
            caption = None
            if media_data.get('edge_media_to_caption', {}).get('edges'):
                caption = media_data['edge_media_to_caption']['edges'][0]['node']['text']
            
            return {
                'url': get_url_from_data(media_data),
                'metadata': {
                    'caption': caption,
                    'username': media_data['owner']['username'],
                    'like': media_data['edge_media_preview_like']['count'],
                    'comment': media_data['edge_media_to_comment']['count'],
                    'isVideo': media_data['is_video']
                }
            }
        except Exception as error:
            raise error
    
    def ig(self, url: str, proxy=None) -> Dict:
        """Main Instagram download function using GraphQL API"""
        post_id = self.get_instagram_post_id(url)
        if not post_id:
            raise ValueError("Invalid Instagram URL")
        
        data = self.get_post_graphql_data(post_id, proxy)
        media_data = data.get('data', {}).get('xdt_shortcode_media')
        
        if not media_data:
            raise ValueError("No media data found")
        
        return self.extract_post_info(media_data)


def instagram_download(url: str) -> Dict:
    """
    Main function to download Instagram content
    Tries GraphQL API first, falls back to snapsave.app if needed
    """
    downloader = InstagramDownloader()
    
    try:
        # Try GraphQL API first
        result = downloader.ig(url)
        return result
    except Exception:
        try:
            # Fallback to snapsave.app
            result = get_download_links(url)
            return result
        except Exception:
            return {
                'msg': 'Try again later'
            }


# For compatibility with the original JavaScript module
Instagram = instagram_download

if __name__ == "__main__":
    # Test the function
    test_url = "https://www.instagram.com/p/example/"
    try:
        result = Instagram(test_url)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")