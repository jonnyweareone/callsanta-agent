"""
🎅 Call Santa Agent v2
======================

A magical LiveKit voice agent with ElevenLabs Santa voice.

Flow:
1. Elf greeting (Deepgram) → Welcomes child
2. Child does activities in 3D grotto  
3. When ready, Santa appears (ElevenLabs Santa voice)
4. Santa conversation
5. Goodbye and recording saved

Agent name: call-santa
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobRequest,
    WorkerOptions,
    cli,
)
from livekit.plugins import deepgram, silero

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("call-santa")

# =============================================================================
# CONFIGURATION
# =============================================================================

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "wss://sip.soniqlabs.co.uk")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://dtosgubmmdqxbeirtbom.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# Audio file paths
AUDIO_DIR = Path(__file__).parent / "audio"
JINGLE_AUDIO = AUDIO_DIR / "christmas-sleigh-bells-jingling-451852.mp3"
THINKING_AUDIO = AUDIO_DIR / "christmas-themed-riser-451859.mp3"
REINDEER_AUDIO = AUDIO_DIR / "reindeer-eating.mp3"
TREE_SPARKLE_AUDIO = AUDIO_DIR / "tree-sparkle.mp3"
BELLS_RINGING_AUDIO = AUDIO_DIR / "bells-ringing.mp3"

# Voice configurations
ELF_VOICE = "aura-2-iris-en"  # Deepgram - for Elf narration
SANTA_VOICE_ID = "Gqe8GJJLg3haJkTwYj2L"  # ElevenLabs Santa Claus voice

# Activity narrations and sounds
ACTIVITY_CONFIG = {
    "feed_reindeer": {
        "narration": "Ooh look! Dasher and Dancer are so hungry! Let's give them some yummy carrots!",
        "sound": "reindeer",
    },
    "decorate_tree": {
        "narration": "Wow, let's add some sparkly decorations to the Christmas tree! It's going to be so pretty!",
        "sound": "sparkle",
    },
    "ring_bells": {
        "narration": "Let's ring the magical sleigh bells! Santa loves the sound of jingle bells!",
        "sound": "bells",
    },
}


# =============================================================================
# ELEVENLABS TTS
# =============================================================================

async def elevenlabs_speak(text: str, audio_source: rtc.AudioSource) -> None:
    """Speak using ElevenLabs Santa voice"""
    import httpx
    from pydub import AudioSegment
    import io
    
    if not ELEVENLABS_API_KEY:
        logger.error("ElevenLabs API key not set")
        return
    
    logger.info(f"[SANTA - ElevenLabs] Speaking: {text}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{SANTA_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "style": 0.5,
                        "use_speaker_boost": True
                    }
                }
            )
            
            if response.status_code != 200:
                logger.error(f"ElevenLabs error: {response.status_code} - {response.text}")
                return
            
            # Convert MP3 to PCM
            audio_data = response.content
            audio = AudioSegment.from_mp3(io.BytesIO(audio_data))
            audio = audio.set_channels(1).set_frame_rate(24000).set_sample_width(2)
            
            # Stream audio
            raw_data = audio.raw_data
            samples_per_frame = 480  # 20ms at 24kHz
            
            for i in range(0, len(raw_data), samples_per_frame * 2):
                chunk = raw_data[i:i + samples_per_frame * 2]
                if len(chunk) == samples_per_frame * 2:
                    frame = rtc.AudioFrame(
                        data=chunk,
                        sample_rate=24000,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    await audio_source.capture_frame(frame)
                    await asyncio.sleep(0.02)
            
            await asyncio.sleep(0.3)
            
    except Exception as e:
        logger.error(f"ElevenLabs TTS error: {e}", exc_info=True)


# =============================================================================
# SANTA AGENT CLASS
# =============================================================================

class SantaAgent:
    """The magical Santa Claus voice agent"""
    
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.room = ctx.room
        
        # Child info
        self.child_name = "friend"
        self.gender = "child"
        self.relationship = "family"
        self.call_id = None
        
        # TTS
        self.elf_tts: Optional[deepgram.TTS] = None
        
        # Audio
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        
        # State
        self.phase = "elf"  # elf, santa, ended
        self.activities_completed = []
        self.gift_wishes = ""
        self.call_active = True
        
    def parse_metadata(self):
        """Extract child info from room metadata"""
        try:
            if self.room.metadata:
                meta = json.loads(self.room.metadata)
                self.child_name = meta.get("child_name", self.child_name)
                self.gender = meta.get("gender", self.gender)
                self.relationship = meta.get("relationship", self.relationship)
                self.call_id = meta.get("call_id")
                logger.info(f"Parsed metadata: {meta}")
                return
            
            for participant in self.room.remote_participants.values():
                if participant.metadata:
                    meta = json.loads(participant.metadata)
                    self.child_name = meta.get("child_name", self.child_name)
                    self.gender = meta.get("gender", self.gender)
                    self.relationship = meta.get("relationship", self.relationship)
                    self.call_id = meta.get("call_id")
                    logger.info(f"Parsed participant metadata: {meta}")
                    return
                    
        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")
    
    async def setup_audio(self):
        """Set up audio track"""
        self.audio_source = rtc.AudioSource(24000, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("santa-voice", self.audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(self.audio_track, options)
        logger.info("Audio track published")
    
    async def setup_tts(self):
        """Initialize Deepgram TTS for Elf"""
        self.elf_tts = deepgram.TTS(
            model=ELF_VOICE,
            api_key=DEEPGRAM_API_KEY,
        )
        logger.info(f"Elf TTS initialized: {ELF_VOICE}")
    
    async def speak_elf(self, text: str):
        """Speak as Elf using Deepgram"""
        if not self.elf_tts or not self.audio_source:
            logger.error("Elf TTS not initialized")
            return
        
        logger.info(f"[ELF] Speaking: {text}")
        
        try:
            audio_data = bytearray()
            stream = self.elf_tts.synthesize(text)
            
            async for audio in stream:
                if audio.frame:
                    audio_data.extend(audio.frame.data)
            
            if not audio_data:
                return
            
            samples_per_frame = 480
            for i in range(0, len(audio_data), samples_per_frame * 2):
                chunk = audio_data[i:i + samples_per_frame * 2]
                if len(chunk) == samples_per_frame * 2:
                    frame = rtc.AudioFrame(
                        data=bytes(chunk),
                        sample_rate=24000,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    await self.audio_source.capture_frame(frame)
                    await asyncio.sleep(0.02)
            
            await asyncio.sleep(0.3)
            
        except Exception as e:
            logger.error(f"Elf TTS error: {e}", exc_info=True)
    
    async def speak_santa(self, text: str):
        """Speak as Santa using ElevenLabs"""
        await elevenlabs_speak(text, self.audio_source)
    
    async def play_audio_file(self, file_path: Path):
        """Play an audio file"""
        if not file_path.exists():
            logger.warning(f"Audio file not found: {file_path}")
            return
        
        logger.info(f"Playing audio: {file_path.name}")
        
        try:
            from pydub import AudioSegment
            
            audio = AudioSegment.from_mp3(str(file_path))
            audio = audio.set_channels(1).set_frame_rate(24000)
            
            raw_data = audio.raw_data
            samples_per_frame = 480
            
            for i in range(0, len(raw_data), samples_per_frame * 2):
                chunk = raw_data[i:i + samples_per_frame * 2]
                if len(chunk) == samples_per_frame * 2:
                    frame = rtc.AudioFrame(
                        data=chunk,
                        sample_rate=24000,
                        num_channels=1,
                        samples_per_channel=samples_per_frame
                    )
                    await self.audio_source.capture_frame(frame)
                    await asyncio.sleep(0.02)
            
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Audio playback error: {e}", exc_info=True)
    
    async def send_data(self, data: dict):
        """Send data message to frontend"""
        try:
            message = json.dumps(data)
            await self.room.local_participant.publish_data(
                message.encode(),
                reliable=True
            )
            logger.info(f"Sent data: {data}")
        except Exception as e:
            logger.error(f"Failed to send data: {e}")
    
    async def handle_data_message(self, payload: bytes):
        """Handle incoming data messages from frontend"""
        try:
            message = json.loads(payload.decode())
            logger.info(f"Received: {message}")
            
            msg_type = message.get("type")
            
            if msg_type == "activity":
                activity = message.get("activity")
                child_name = message.get("childName", self.child_name)
                
                config = ACTIVITY_CONFIG.get(activity, {})
                narration = config.get("narration", "Wow, how fun!")
                sound_type = config.get("sound")
                
                # Narrate the activity
                await self.speak_elf(narration)
                
                # Play activity sound effect
                if sound_type == "reindeer":
                    await self.play_audio_file(REINDEER_AUDIO)
                elif sound_type == "sparkle":
                    await self.play_audio_file(TREE_SPARKLE_AUDIO)
                elif sound_type == "bells":
                    await self.play_audio_file(BELLS_RINGING_AUDIO)
                
                # Track completion
                if activity not in self.activities_completed:
                    self.activities_completed.append(activity)
                
                # Notify completion
                await asyncio.sleep(0.5)
                await self.send_data({"type": "activity_complete", "activity": activity})
                
            elif msg_type == "ready_for_santa":
                logger.info("Child is ready for Santa!")
                await self.start_santa_conversation()
                
        except Exception as e:
            logger.error(f"Failed to handle data: {e}")
    
    async def start_santa_conversation(self):
        """Start the Santa conversation phase"""
        self.phase = "santa"
        
        # Elf hands off to Santa
        await self.speak_elf(
            f"Oh! {self.child_name}! Santa is ready to talk to you now! "
            "Here he comes!"
        )
        
        # Play jingle bells for Santa's arrival
        await self.play_audio_file(JINGLE_AUDIO)
        
        # Notify frontend Santa is here
        await self.send_data({"type": "phase_change", "phase": "santa"})
        
        await asyncio.sleep(0.5)
        
        # Santa greeting
        child_term = "boy" if self.gender == "boy" else "girl"
        
        await self.speak_santa(
            f"Ho Ho Ho! Hello {self.child_name}! "
            f"Merry Christmas! I've heard you've been a very good {child_term} this year!"
        )
        
        await asyncio.sleep(0.5)
        
        # Mention activities
        if self.activities_completed:
            activity_mentions = []
            if "feed_reindeer" in self.activities_completed:
                activity_mentions.append("feeding my reindeer")
            if "decorate_tree" in self.activities_completed:
                activity_mentions.append("decorating the tree")
            if "ring_bells" in self.activities_completed:
                activity_mentions.append("ringing the sleigh bells")
            
            if activity_mentions:
                await self.speak_santa(
                    f"Jingle told me you had lots of fun {' and '.join(activity_mentions)}! "
                    "The elves and reindeer just love helpers like you!"
                )
        
        await asyncio.sleep(0.5)
        
        # Ask about wishes
        await self.speak_santa(
            "Now tell me, what would you like for Christmas?"
        )
        
        # Give child time to respond (in real implementation, use STT)
        await asyncio.sleep(10)
        
        # Santa's response
        await self.speak_santa(
            f"Those are wonderful wishes! "
            f"I will talk to your {self.relationship} to make sure everything is ready. "
            "Remember to be good and get lots of sleep on Christmas Eve!"
        )
        
        await asyncio.sleep(0.5)
        
        # Goodbye
        await self.speak_santa(
            f"Merry Christmas {self.child_name}! Ho Ho Ho! "
            "See you soon!"
        )
        
        # Play final jingles
        await self.play_audio_file(JINGLE_AUDIO)
        
        # Notify end
        self.phase = "ended"
        await self.send_data({"type": "phase_change", "phase": "ended"})
        
        logger.info("Santa conversation completed!")
    
    async def run(self):
        """Main agent loop"""
        logger.info(f"🎅 Santa Agent starting in room: {self.room.name}")
        
        # Wait for participant
        await asyncio.sleep(1)
        
        # Parse metadata
        self.parse_metadata()
        
        # Setup
        await self.setup_audio()
        await self.setup_tts()
        
        # Register data handler
        @self.room.on("data_received")
        def on_data(data: rtc.DataPacket):
            asyncio.create_task(self.handle_data_message(data.data))
        
        # Initial elf greeting
        await self.play_audio_file(JINGLE_AUDIO)
        
        await self.speak_elf(
            f"Hello {self.child_name}! Welcome to Santa's Workshop! "
            f"I'm Jingle the Elf! While Santa gets ready, "
            "why don't you explore and have some fun? "
            "Pick an activity to try!"
        )
        
        # Wait for conversation to complete or timeout
        timeout = 300  # 5 minutes max
        start_time = asyncio.get_event_loop().time()
        
        while self.call_active and self.phase != "ended":
            await asyncio.sleep(1)
            
            if asyncio.get_event_loop().time() - start_time > timeout:
                logger.info("Call timeout reached")
                break
            
            # Check if room still has participants
            if len(self.room.remote_participants) == 0:
                logger.info("No more participants, ending call")
                break
        
        logger.info("🎅 Santa Agent finished")


# =============================================================================
# LIVEKIT ENTRY POINTS
# =============================================================================

async def entrypoint(ctx: JobContext):
    """Main entry point"""
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(f"🎅 Connected to room: {ctx.room.name}")
    
    await asyncio.sleep(1)
    
    agent = SantaAgent(ctx)
    await agent.run()


async def request_handler(req: JobRequest):
    """Handle job requests"""
    try:
        metadata = json.loads(req.room.metadata or "{}")
    except:
        metadata = {}
    
    agent_type = metadata.get("agent_type", "").lower()
    agent_name = metadata.get("agent_name", "")
    
    if agent_type == "santa" or agent_name == "call-santa" or "santa" in req.room.name.lower():
        logger.info(f"🎅 Accepting call: {req.room.name}")
        await req.accept()
    else:
        logger.info(f"❌ Rejecting: {req.room.name}")
        await req.reject()


if __name__ == "__main__":
    logger.info("🎅 Call Santa Agent v2 Starting...")
    logger.info(f"   LiveKit: {LIVEKIT_URL}")
    logger.info(f"   Deepgram: {'✓' if DEEPGRAM_API_KEY else '✗'}")
    logger.info(f"   ElevenLabs: {'✓' if ELEVENLABS_API_KEY else '✗'}")
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_handler,
        )
    )
