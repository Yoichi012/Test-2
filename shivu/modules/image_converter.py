from shivu.config import Development as Config
import asyncio
import os
import aiohttp
from typing import Optional
from datetime import datetime

from shivu import collection


class ImageConverter:
    """Async background utility for converting Telegram images to catbox.moe"""
    
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = aiohttp.ClientTimeout(total=30)
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    async def download_telegram_image(self, file_id: str) -> Optional[bytes]:
        if not self.bot_token:
            return None
            
        try:
            async with self.session.get(
                f'https://api.telegram.org/bot{self.bot_token}/getFile',
                params={'file_id': file_id}
            ) as response:
                if response.status != 200:
                    return None
                    
                data = await response.json()
                if not data.get('ok'):
                    return None
                    
                file_path = data['result']['file_path']
                
            async with self.session.get(
                f'https://api.telegram.org/file/bot{self.bot_token}/{file_path}'
            ) as response:
                if response.status == 200:
                    return await response.read()
                    
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError):
            return None
            
        return None
        
    async def upload_to_catbox(self, image_data: bytes) -> Optional[str]:
        try:
            form_data = aiohttp.FormData()
            form_data.add_field('reqtype', 'fileupload')
            form_data.add_field('fileToUpload', 
                              image_data,
                              filename='image.jpg',
                              content_type='image/jpeg')
            
            async with self.session.post(
                'https://catbox.moe/user/api.php',
                data=form_data
            ) as response:
                if response.status == 200:
                    url = (await response.text()).strip()
                    if url and url.startswith('http'):
                        return url
                        
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
            return None
            
        return None
        
    async def update_character_image(self, character_id: str, image_url: str) -> bool:
        try:
            result = await collection.update_one(
                {'id': character_id},
                {'$set': {'img_url': image_url, 'updated_at': datetime.utcnow()}}
            )
            return result.modified_count > 0
            
        except Exception:
            return False
        
    async def _perform_conversion(self, character_id: str, file_id: str) -> bool:
        image_data = await self.download_telegram_image(file_id)
        if not image_data:
            return False
            
        image_url = await self.upload_to_catbox(image_data)
        if not image_url:
            return False
            
        return await self.update_character_image(character_id, image_url)


async def convert_and_update(character_id: str, file_id: str) -> bool:
    async with ImageConverter() as converter:
        try:
            result = await converter._perform_conversion(character_id, file_id)
            
            if result:
                print(f"[IMAGE_CONVERTER] ✅ SUCCESS")
                print(f"Character ID: {character_id}")
                print(f"Public URL saved")
            else:
                print(f"[IMAGE_CONVERTER] ❌ FAILED")
                print(f"Character ID: {character_id}")
                print(f"Reason: download/upload/db error")
                
            return result
            
        except Exception as e:
            print(f"[IMAGE_CONVERTER] ❌ FAILED")
            print(f"Character ID: {character_id}")
            print(f"Reason: {str(e)}")
            return False


def start_image_conversion(character_id: str, file_id: str) -> None:
    task = asyncio.create_task(convert_and_update(character_id, file_id))
    
    def _silent_completion(task: asyncio.Task) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass
    
    task.add_done_callback(_silent_completion)