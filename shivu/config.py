class Config(object):
    LOGGER = True

    # Get this value from my.telegram.org/apps
    OWNER_ID = "7818323042"
    sudo_users = "7818323042", "8453236527"
    GROUP_ID = -1003129952280
    TOKEN = "8352347985:AAEHt7hWqwcrg4w56BeltW9WyNr-fuf9n6s"
    mongo_url = "mongodb+srv://ravi:ravi12345@cluster0.hndinhj.mongodb.net/?retryWrites=true&w=majority"
    PHOTO_URL = ["https://telegra.ph/file/b925c3985f0f325e62e17.jpg", "https://telegra.ph/file/4211fb191383d895dab9d.jpg"]
    SUPPORT_CHAT = "https://t.me/THE_DRAGON_SUPPORT"
    UPDATE_CHAT = "https://t.me/PICK_X_UPDATE"
    BOT_USERNAME = "Collect_Em_AllBot"
    CHARA_CHANNEL_ID = "-1003150808065"
    api_id = 35660683
    api_hash = "7afb42cd73fb5f3501062ffa6a1f87f7"

    
class Production(Config):
    LOGGER = True


class Development(Config):
    LOGGER = True
