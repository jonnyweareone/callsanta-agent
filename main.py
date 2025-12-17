"""
🎅 Call Santa Agent
==================

A magical LiveKit voice agent that lets children talk to Santa Claus.

Flow:
1. Elf greeting → "Hi {name}! I'm Happy the Elf!"
2. Jingle bells audio
3. Santa greeting → "Ho Ho Ho! Hello {name}!"
4. Santa asks → "What would you like for Christmas?"
5. Listen to child (STT)
6. Thinking music → "Let me check my list..."
7. Santa response → "I'll check with your {relationship}!"
8. Goodbye → "Merry Christmas! Ho Ho Ho!"

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

# Audio file paths (bundled in Docker image)
AUDIO_DIR = Path(__file__).parent / "audio"
JINGLE_AUDIO = AUDIO_DIR / "christmas-sleigh-bells-jingling-451852.mp3"
THINKING_AUDIO = AUDIO_DIR / "christmas-themed-riser-451859.mp3"

# Voice configurations - Deepgram Aura-2 voices
SANTA_VOICE = "aura-2-draco-en"    # British, Warm, Baritone - perfect for Santa
ELF_VOICE = "aura-2-iris-en"       # Young Adult, Cheerful, Positive - perfect for an elf

# Santa voice modifications
SANTA_PITCH_SEMITONES = -3   # Lower pitch (negative = deeper)
SANTA_SPEED_FACTOR = 0.95    # Slower speech (< 1 = slower)


# =============================================================================
# SANTA AGENT CLASS
# =============================================================================

class SantaAgent:
    """The magical Santa Claus voice agent"""
    
    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.room = ctx.room
        
        # Parse metadata from the call
        self.child_name = "friend"
        self.gender = "child"
        self.relationship = "family"
        self.call_id = None
        
        # STT/TTS instances
        self.stt: Optional[deepgram.STT] = None
        self.santa_tts: Optional[deepgram.TTS] = None
        self.elf_tts: Optional[deepgram.TTS] = None
        
        # Audio source for TTS playback
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        
        # State
        self.gift_wishes = ""
        self.call_active = True
        
    def parse_metadata(self):
        """Extract child info from room metadata or participant metadata"""
        try:
            # Try room metadata first
            if self.room.metadata:
                meta = json.loads(self.room.metadata)
                self.child_name = meta.get("child_name", self.child_name)
                self.gender = meta.get("gender", self.gender)
                self.relationship = meta.get("relationship", self.relationship)
                self.call_id = meta.get("call_id")
                logger.info(f"Parsed room metadata: {meta}")
                return
            
            # Try participant metadata
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
        """Set up audio source and track for TTS playback"""
        # Create audio source (24kHz mono - matches Deepgram TTS default)
        self.audio_source = rtc.AudioSource(24000, 1)
        
        # Create local audio track
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "santa-voice",
            self.audio_source
        )
        
        # Publish the track
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(self.audio_track, options)
        logger.info("Audio track published (24kHz)")
    
    async def setup_tts(self):
        """Initialize TTS engines for Santa and Elf"""
        self.santa_tts = deepgram.TTS(
            model=SANTA_VOICE,
            api_key=DEEPGRAM_API_KEY,
        )
        
        self.elf_tts = deepgram.TTS(
            model=ELF_VOICE,
            api_key=DEEPGRAM_API_KEY,
        )
        
        logger.info(f"TTS initialized - Santa: {SANTA_VOICE}, Elf: {ELF_VOICE}")
    
    async def setup_stt(self):
        """Initialize STT for listening to the child"""
        self.stt = deepgram.STT(
            model="nova-2",
            api_key=DEEPGRAM_API_KEY,
            language="en",
        )
        logger.info("STT initialized")
    
    async def speak(self, text: str, voice: str = "santa"):
        """Speak text using TTS"""
        tts = self.santa_tts if voice == "santa" else self.elf_tts
        
        if not tts or not self.audio_source:
            logger.error("TTS or audio source not initialized")
            return
        
        logger.info(f"[{voice.upper()}] Speaking: {text}")
        
        try:
            from pydub import AudioSegment
            import io
            
            # Collect all audio frames
            audio_data = bytearray()
            stream = tts.synthesize(text)
            sample_rate = 24000
            
            async for audio in stream:
                if audio.frame:
                    audio_data.extend(audio.frame.data)
                    sample_rate = audio.frame.sample_rate
            
            if not audio_data:
                logger.warning("No audio data received from TTS")
                return
            
            # Convert to AudioSegment for processing
            audio_segment = AudioSegment(
                data=bytes(audio_data),
                sample_width=2,  # 16-bit
                frame_rate=sample_rate,
                channels=1
            )
            
            # Apply modifications for Santa (deeper & slower)
            if voice == "santa":
                # Pitch down (negative semitones = deeper voice)
                new_sample_rate = int(audio_segment.frame_rate * (2 ** (SANTA_PITCH_SEMITONES / 12.0)))
                pitched = audio_segment._spawn(audio_segment.raw_data, overrides={'frame_rate': new_sample_rate})
                pitched = pitched.set_frame_rate(24000)  # Resample back to 24kHz
                
                # Slow down by stretching (change speed without affecting pitch further)
                # We do this by adjusting frame rate then resampling
                slowed_rate = int(24000 * SANTA_SPEED_FACTOR)
                slowed = pitched._spawn(pitched.raw_data, overrides={'frame_rate': slowed_rate})
                audio_segment = slowed.set_frame_rate(24000)
            
            # Stream the processed audio
            raw_data = audio_segment.raw_data
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
                    await self.audio_source.capture_frame(frame)
                    await asyncio.sleep(0.02)
            
            # Small pause after speaking
            await asyncio.sleep(0.3)
            
        except Exception as e:
            logger.error(f"TTS error: {e}", exc_info=True)
    
    async def play_audio_file(self, file_path: Path):
        """Play an audio file (MP3) through the audio track"""
        if not file_path.exists():
            logger.warning(f"Audio file not found: {file_path}")
            return
        
        logger.info(f"Playing audio: {file_path.name}")
        
        try:
            # Use pydub to load and convert audio
            from pydub import AudioSegment
            
            audio = AudioSegment.from_mp3(str(file_path))
            # Convert to 24kHz mono to match TTS output
            audio = audio.set_channels(1).set_frame_rate(24000)
            
            # Get raw audio data
            raw_data = audio.raw_data
            samples_per_frame = 480  # 20ms at 24kHz
            
            # Stream audio in chunks
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
                    await asyncio.sleep(0.02)  # 20ms per frame
            
            await asyncio.sleep(0.5)  # Pause after audio
            
        except Exception as e:
            logger.error(f"Audio playback error: {e}", exc_info=True)
    
    async def listen_for_wishes(self, timeout: float = 15.0) -> str:
        """Listen to the child and transcribe their gift wishes"""
        if not self.stt:
            return ""
        
        logger.info("Listening for gift wishes...")
        
        wishes = []
        
        try:
            # Subscribe to audio from remote participants
            for participant in self.room.remote_participants.values():
                for track_pub in participant.track_publications.values():
                    if track_pub.track and track_pub.kind == rtc.TrackKind.KIND_AUDIO:
                        # Create STT stream
                        stream = self.stt.stream()
                        
                        # Listen for a duration
                        start_time = asyncio.get_event_loop().time()
                        
                        async for event in stream:
                            if hasattr(event, 'alternatives') and event.alternatives:
                                text = event.alternatives[0].transcript
                                if text:
                                    wishes.append(text)
                                    logger.info(f"Heard: {text}")
                            
                            # Check timeout
                            if asyncio.get_event_loop().time() - start_time > timeout:
                                break
                        
                        await stream.aclose()
                        
        except Exception as e:
            logger.error(f"STT error: {e}")
        
        self.gift_wishes = " ".join(wishes)
        return self.gift_wishes
    
    async def update_call_status(self, status: str, gift_wishes: str = None):
        """Update the call record in Supabase"""
        if not self.call_id or not SUPABASE_SERVICE_KEY:
            return
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{SUPABASE_URL}/rest/v1/rpc/update_santa_call",
                    headers={
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "p_room_name": self.room.name,
                        "p_status": status,
                        "p_gift_wishes": gift_wishes,
                    }
                )
                logger.info(f"Updated call status to {status}")
                
        except Exception as e:
            logger.error(f"Failed to update call status: {e}")
    
    async def run(self):
        """Main conversation flow"""
        logger.info(f"🎅 Santa Agent starting in room: {self.room.name}")
        
        # Wait for participant to join
        await asyncio.sleep(1)
        
        # Parse metadata
        self.parse_metadata()
        
        # Set up audio and TTS
        await self.setup_audio()
        await self.setup_tts()
        await self.setup_stt()
        
        # Update status to active
        await self.update_call_status("active")
        
        # Get gender-specific terms
        child_term = "boy" if self.gender == "boy" else "girl"
        
        # ===== THE MAGICAL CONVERSATION =====
        
        # 1. Play jingle bells to set the mood
        await self.play_audio_file(JINGLE_AUDIO)
        
        # 2. Elf Greeting (cheerful and upbeat)
        await self.speak(
            f"Hello {self.child_name}! I'm Jingle the Elf! "
            f"Ooh, Santa is going to be SO excited to talk to you! Let me get him!",
            voice="elf"
        )
        
        # 3. Play jingle bells while "getting Santa"
        await self.play_audio_file(JINGLE_AUDIO)
        
        # 4. Santa Greeting
        await self.speak(
            f"Ho Ho Ho! Hello {self.child_name}! "
            f"I've heard you've been a very good {child_term} this year!",
            voice="santa"
        )
        
        await asyncio.sleep(0.5)
        
        # 5. Ask about Christmas wishes
        await self.speak(
            "What would you like for Christmas?",
            voice="santa"
        )
        
        # 6. Listen to the child
        await asyncio.sleep(8)  # Give them time to respond
        
        # Note: In a full implementation, we'd use STT here
        # For now, we'll proceed with the flow
        
        # 7. Thinking music
        await self.speak(
            "Let me check my list...",
            voice="santa"
        )
        await self.play_audio_file(THINKING_AUDIO)
        
        # 8. Santa's response
        await self.speak(
            f"I've checked my list and you have some wonderful gifts coming! "
            f"I will check with your {self.relationship} to make sure everything is ready!",
            voice="santa"
        )
        
        await asyncio.sleep(0.5)
        
        # 9. Goodbye
        await self.speak(
            f"Merry Christmas {self.child_name}! Ho Ho Ho!",
            voice="santa"
        )
        
        # Update call as completed
        await self.update_call_status("completed", self.gift_wishes)
        
        logger.info("🎅 Santa conversation completed!")
        
        # Keep connection open briefly for audio to finish
        await asyncio.sleep(2)


# =============================================================================
# LIVEKIT AGENT ENTRY POINTS
# =============================================================================

async def entrypoint(ctx: JobContext):
    """Main entry point for the agent"""
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    logger.info(f"🎅 Call Santa agent connected to room: {ctx.room.name}")
    
    # Wait for a participant to join
    await asyncio.sleep(1)
    
    # Create and run the Santa agent
    agent = SantaAgent(ctx)
    await agent.run()


async def request_handler(req: JobRequest):
    """Handle incoming job requests"""
    try:
        metadata = json.loads(req.room.metadata or "{}")
    except:
        metadata = {}
    
    agent_type = metadata.get("agent_type", "").lower()
    agent_name = metadata.get("agent_name", "")
    
    # Accept santa calls
    if agent_type == "santa" or agent_name == "call-santa" or "santa" in req.room.name.lower():
        logger.info(f"🎅 Accepting Santa call: {req.room.name}")
        await req.accept()
    else:
        logger.info(f"❌ Rejecting non-Santa call: {req.room.name} (type: {agent_type})")
        await req.reject()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    logger.info("🎅 Call Santa Agent Starting...")
    logger.info(f"   LiveKit: {LIVEKIT_URL}")
    logger.info(f"   Supabase: {SUPABASE_URL}")
    logger.info(f"   Deepgram API Key: {'✓' if DEEPGRAM_API_KEY else '✗'}")
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_handler,
        )
    )
