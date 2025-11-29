class Tag:
    def __init__(self, t: float, micro: int):
        self.t = t
        self.micro = micro
        
    def next_micro(self):
        return Tag(self.t, self.micro + 1)
        
    def next_t(self, delta: float):
        return Tag(self.t + delta, 0)
        
    def __repr__(self):
        return f"Tag(t={self.t}, micro={self.micro})"
    
    def __eq__(self, other):
        return isinstance(other, Tag) and self.t == other.t and self.micro == other.micro
        
    def __lt__(self, other):
        if not isinstance(other, Tag): return NotImplemented
        if self.t != other.t:
            return self.t < other.t
        return self.micro < other.micro
    
    def __hash__(self):
        return hash((self.t, self.micro))
