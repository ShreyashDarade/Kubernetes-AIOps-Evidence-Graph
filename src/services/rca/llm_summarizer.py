"""
LLM Summarizer for hypothesis enhancement.
Uses LLM to summarize evidence and enhance hypotheses.
"""
from typing import Any, Optional
import json
import structlog
import httpx

from src.config import settings


logger = structlog.get_logger()


class LLMSummarizer:
    """Uses LLM to enhance RCA hypotheses."""
    
    def __init__(self):
        self.provider = settings.llm_provider
    
    async def enhance_hypotheses(
        self,
        hypotheses: list[dict],
        evidence: list[dict],
    ) -> list[dict]:
        """Enhance hypotheses with LLM-generated insights."""
        if not hypotheses:
            return hypotheses
        
        # Prepare evidence summary
        evidence_summary = self._summarize_evidence(evidence)
        
        # Enhance top hypotheses
        for h in hypotheses[:3]:  # Only enhance top 3
            try:
                enhanced = await self._enhance_single(h, evidence_summary)
                h.update(enhanced)
            except Exception as e:
                logger.warning("LLM enhancement failed for hypothesis", error=str(e))
        
        return hypotheses
    
    def _summarize_evidence(self, evidence: list[dict]) -> str:
        """Create a text summary of evidence."""
        summaries = []
        
        for ev in evidence[:20]:  # Limit to 20 items
            summary = ev.get("summary", "")
            if summary:
                summaries.append(f"- {summary}")
        
        return "\n".join(summaries) if summaries else "No evidence summary available."
    
    async def _enhance_single(
        self,
        hypothesis: dict,
        evidence_summary: str,
    ) -> dict:
        """Enhance a single hypothesis using LLM."""
        prompt = f"""You are a Kubernetes incident analyst. Given the following hypothesis and evidence, provide:
1. A concise reasoning chain explaining why this hypothesis is likely
2. Additional investigation steps
3. Potential alternative explanations

Hypothesis: {hypothesis.get('title')}
Category: {hypothesis.get('category')}
Description: {hypothesis.get('description')}
Confidence: {hypothesis.get('confidence')}

Evidence:
{evidence_summary}

Respond in JSON format:
{{
    "reasoning": "Step by step reasoning for this hypothesis",
    "additional_steps": ["step1", "step2"],
    "alternatives": ["alternative1", "alternative2"],
    "enhanced_description": "More detailed description"
}}
"""
        
        if self.provider == "gemini":
            return await self._call_gemini(prompt)
        elif self.provider == "openai":
            return await self._call_openai(prompt)
        elif self.provider == "ollama":
            return await self._call_ollama(prompt)
        else:
            return {}
    
    async def _call_gemini(self, prompt: str) -> dict:
        """Call Google Gemini API."""
        if not settings.google_api_key:
            return {}
        
        url = f"https://generativelanguage.googleapis.com/v1/models/{settings.gemini_model}:generateContent"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                params={"key": settings.google_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 1000,
                    }
                },
            )
            response.raise_for_status()
            
            data = response.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            
            # Parse JSON from response
            try:
                # Find JSON in response
                start = text.find("{")
                end = text.rfind("}") + 1
                if start != -1 and end > start:
                    return json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM JSON response")
            
            return {}
    
    async def _call_openai(self, prompt: str) -> dict:
        """Call OpenAI API."""
        if not settings.openai_api_key:
            return {}
        
        url = "https://api.openai.com/v1/chat/completions"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": "You are a Kubernetes incident analyst. Respond only with valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                },
            )
            response.raise_for_status()
            
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            try:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start != -1 and end > start:
                    return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
            
            return {}
    
    async def _call_ollama(self, prompt: str) -> dict:
        """Call Ollama local LLM."""
        url = f"{settings.ollama_url}/api/generate"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            
            data = response.json()
            text = data.get("response", "")
            
            try:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start != -1 and end > start:
                    return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
            
            return {}
