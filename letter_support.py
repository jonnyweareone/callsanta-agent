"""
🎅 Call Santa Agent v3 - Letter Support Patch
=============================================

This module adds letter-reading functionality to the Santa agent.
Import these functions into main.py or use them as a reference.
"""

import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("call-santa")

# Frontend API for letter lookup
FRONTEND_API_URL = "https://callsanta.vercel.app"


async def fetch_letter(letter_id: str) -> Optional[Dict[str, Any]]:
    """Fetch letter data from the API"""
    if not letter_id:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{FRONTEND_API_URL}/api/letter?id={letter_id}")
            if response.status_code == 200:
                data = response.json()
                return data.get("letter")
    except Exception as e:
        logger.error(f"Failed to fetch letter: {e}")
    
    return None


def format_behavior(behavior: str) -> str:
    """Convert behavior code to friendly text"""
    behaviors = {
        "super_good": "super duper good",
        "pretty_good": "pretty good", 
        "mostly_good": "mostly good",
    }
    return behaviors.get(behavior, "good")


def format_snack(snack: str) -> str:
    """Convert snack code to friendly text"""
    snacks = {
        "cookies": "cookies",
        "mince_pies": "mince pies",
        "carrots_for_reindeer": "carrots for my reindeer",
    }
    return snacks.get(snack, "cookies")


def generate_letter_scripts(letter: Dict[str, Any], child_name: str) -> Dict[str, str]:
    """
    Generate script snippets for when Santa reads the letter.
    
    Returns dict with:
    - elf_letter_notice: Elf noticing the letter arrived
    - santa_letter_intro: Santa acknowledging the letter
    - santa_nice_thing: Mentioning their good deed
    - santa_wishes: Mentioning their wishes
    - santa_snack: Mentioning the snack
    """
    scripts = {}
    
    # Elf notices letter
    scripts["elf_letter_notice"] = (
        f"Oh wait! *magical chime* I just received a special letter! "
        f"Is this from... {child_name}? "
        f"Let me make sure Santa sees this right away!"
    )
    
    # Santa intro
    behavior = format_behavior(letter.get("behavior", "good"))
    scripts["santa_letter_intro"] = (
        f"I just read your wonderful letter, {child_name}! "
        f"You said you've been {behavior} this year!"
    )
    
    # Nice thing
    nice_thing = letter.get("niceThing", "")
    if nice_thing:
        scripts["santa_nice_thing"] = (
            f"I was so happy to hear that you {nice_thing}. "
            "That was very kind of you, and I've added extra stars to your name on my Nice List!"
        )
    else:
        scripts["santa_nice_thing"] = ""
    
    # Wishes
    wishes = letter.get("wishes", [])
    if wishes and len(wishes) > 0:
        # Get first 2 non-empty wishes
        valid_wishes = [w for w in wishes if w and w.strip()][:2]
        if len(valid_wishes) >= 2:
            wishes_text = f"{valid_wishes[0]} and {valid_wishes[1]}"
        elif len(valid_wishes) == 1:
            wishes_text = valid_wishes[0]
        else:
            wishes_text = ""
        
        if wishes_text:
            scripts["santa_wishes"] = (
                f"Now, I see you would really love a {wishes_text}! "
                "Those are wonderful wishes! I'll see what I can do!"
            )
        else:
            scripts["santa_wishes"] = ""
    else:
        scripts["santa_wishes"] = ""
    
    # Snack
    snack = format_snack(letter.get("snack", "cookies"))
    scripts["santa_snack"] = (
        f"And thank you for leaving out {snack} for me! "
        "I do get very hungry on Christmas Eve!"
    )
    
    return scripts


# =============================================================================
# USAGE EXAMPLE IN MAIN AGENT:
# =============================================================================

"""
# In SantaAgent.__init__:
self.letter_id = None
self.letter = None

# In parse_metadata:
self.letter_id = meta.get("letter_id")

# After parse_metadata in run():
if self.letter_id:
    self.letter = await fetch_letter(self.letter_id)
    if self.letter:
        scripts = generate_letter_scripts(self.letter, self.child_name)
        # Store for later use
        self.letter_scripts = scripts

# In elf greeting, after initial greeting:
if self.letter:
    await self.speak_elf(self.letter_scripts["elf_letter_notice"])

# In start_santa_conversation, after initial Santa greeting:
if self.letter:
    await self.speak_santa(self.letter_scripts["santa_letter_intro"])
    await asyncio.sleep(0.5)
    
    if self.letter_scripts["santa_nice_thing"]:
        await self.speak_santa(self.letter_scripts["santa_nice_thing"])
        await asyncio.sleep(0.5)
    
    if self.letter_scripts["santa_wishes"]:
        await self.speak_santa(self.letter_scripts["santa_wishes"])
        await asyncio.sleep(0.5)
    
    await self.speak_santa(self.letter_scripts["santa_snack"])
    await asyncio.sleep(0.5)
"""
