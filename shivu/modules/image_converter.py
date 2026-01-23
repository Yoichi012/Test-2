import asyncio
import os
import aiohttp
from typing import Optional
from datetime import datetime

# Import MongoDB collection
from shivu import collection


class ImageConverter:
    """Async background utility for converting Telegram images to catbox.moe"""
    
    def __init__(self):
        """Initialize ImageConverter with bot token from environment"""
        self.bot_token = os.getenv('BOT_TOKEN')
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = aiohttp.ClientTimeout(total=30)
        
    async def __aenter__(self):
        """Async context manager entry"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session and not self.session.closed:
            await self.session.close()
            
    async def download_telegram_image(self, file_id: str) -> Optional[bytes]:
        """
        Download image from Telegram using file_id.
        
        Args:
            file_id: Telegram file_id
            
        Returns:
            Image bytes or None if download fails
        """
        if not self.bot_token:
            return None
            
        try:
            # Create session if not exists
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession(timeout=self.timeout)
            
            # Get file path from Telegram
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
                
            # Download actual file
            async with self.session.get(
                f'https://api.telegram.org/file/bot{self.bot_token}/{file_path}'
            ) as response:
                if response.status == 200:
                    return await response.read()
                    
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError):
            return None
            
        return None
        
    async def upload_to_catbox(self, image_data: bytes) -> Optional[str]:
        """
        Upload image to catbox.moe and get direct URL.
        
        Args:
            image_data: Raw image bytes
            
        Returns:
            Direct image URL or None if upload fails
        """
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
            
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
                    # Validate URL
                    if url and url.startswith('http'):
                        return url
                        
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
            return None
            
        return None
        
    async def update_character_image(self, character_id: str, image_url: str) -> bool:
        """
        Update character's image URL in MongoDB.
        
        Args:
            character_id: Character ID
            image_url: New image URL
            
        Returns:
            True if update successful, False otherwise
        """
        try:
            # Update character document in MongoDB
            result = await collection.update_one(
                {'id': character_id},
                {'$set': {'img_url': image_url, 'updated_at': datetime.utcnow()}}
            )
            
            return result.modified_count > 0
            
        except Exception:
            return False
        
    async def convert_and_update(self, character_id: str, file_id: str) -> bool:
        """
        Main method: Download, upload, and update image URL.
        
        Args:
            character_id: Character ID
            file_id: Telegram file_id
            
        Returns:
            True if all operations successful, False otherwise
        """
        try:
            # Create context manager manually if called directly
            if not self.session:
                async with self:
                    return await self._perform_conversion(character_id, file_id)
            else:
                return await self._perform_conversion(character_id, file_id)
                
        except Exception:
            return False
            
    async def _perform_conversion(self, character_id: str, file_id: str) -> bool:
        """Internal method to perform the conversion steps"""
        # Download from Telegram
        image_data = await self.download_telegram_image(file_id)
        if not image_data:
            return False
            
        # Upload to catbox.moe
        image_url = await self.upload_to_catbox(image_data)
        if not image_url:
            return False
            
        # Update database
        return await self.update_character_image(character_id, image_url)


async def convert_and_update(character_id: str, file_id: str) -> bool:
    """
    Convert Telegram image to public URL and update character.
    
    Args:
        character_id: Character ID
        file_id: Telegram file_id
        
    Returns:
        True if successful, False otherwise
    """
    async with ImageConverter() as converter:
        return await converter.convert_and_update(character_id, file_id)


def start_image_conversion(character_id: str, file_id: str) -> None:
    """
    Start image conversion in background (fire-and-forget).
    
    Args:
        character_id: Character ID
        file_id: Telegram file_id
    """
    # Create and schedule background task
    task = asyncio.create_task(convert_and_update(character_id, file_id))
    
    # Add error callback to prevent "Task exception was never retrieved" warnings
    def _handle_task_result(task: asyncio.Task) -> None:
        try:
            task.result()  # This will re-raise any exception
        except (asyncio.CancelledError, Exception):
            pass  # Silently ignore all errors as per requirements
    
    task.add_done_callback(_handle_task_result)