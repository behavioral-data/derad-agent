"""
Tracked Embedder - Wraps the real embedder to track all actual API calls.
"""
from typing import List
from langchain_core.embeddings import Embeddings

class TrackedEmbedder(Embeddings):
    """Wrapper around the real embedder for API compatibility."""
    
    def __init__(self, real_embedder):
        self.real_embedder = real_embedder
        
    @property
    def __dict__(self):
        """Forward __dict__ to the real embedder for compatibility."""
        return self.real_embedder.__dict__
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple documents."""
        return self.real_embedder.embed_documents(texts)
    
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query."""
        return self.real_embedder.embed_query(text)
    
    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Async embed multiple documents (forward to real embedder)."""
        if hasattr(self.real_embedder, 'aembed_documents'):
            return await self.real_embedder.aembed_documents(texts)
        else:
            # Fallback to sync version
            return self.embed_documents(texts)
    
    async def aembed_query(self, text: str) -> List[float]:
        """Async embed a single query (forward to real embedder)."""
        if hasattr(self.real_embedder, 'aembed_query'):
            return await self.real_embedder.aembed_query(text)
        else:
            # Fallback to sync version
            return self.embed_query(text)
    
    def _get_model_name(self) -> str:
        """Get the model name from the embedder."""
        # For Azure OpenAI, use the deployment name which represents the intended model
        if hasattr(self.real_embedder, 'deployment'):
            deployment = getattr(self.real_embedder, 'deployment')
            if deployment:
                return deployment
        
        # Fallback to azure_deployment attribute if available
        if hasattr(self.real_embedder, 'azure_deployment'):
            deployment = getattr(self.real_embedder, 'azure_deployment')
            if deployment:
                return deployment
        
        # Try to get model name from various possible attributes for regular OpenAI
        for attr in ['model', 'deployment_name']:
            if hasattr(self.real_embedder, attr):
                model_name = getattr(self.real_embedder, attr)
                if model_name:
                    return model_name
        
        # Default fallback (use latest small embedding model)
        return "text-embedding-3-small"
    
    def __getattr__(self, name):
        """Forward any other method calls to the real embedder."""
        return getattr(self.real_embedder, name)
    
    def __call__(self, text: str) -> List[float]:
        """Make the embedder callable (for FAISS compatibility)."""
        return self.embed_query(text) 
