# EventTracker Flashcards

Interactive flashcard application to test understanding of the EventTracker application.

## 🎯 Usage

Simply open `index.html` in your browser - no server required!

```bash
# From the project root
open tutorial/index.html
```

Or double-click the `index.html` file directly.

## 📝 Features

- **25 Flashcards** covering:
  - Core architecture and design
  - Technology stack (FastAPI, SQLite, Jinja2, Bootstrap)
  - Service layer and domain capabilities
  - Database design and FTS5/embeddings
  - Testing frameworks (pytest, Playwright)
  - Security practices (CSRF, environment variables)
  - AI and semantic search features
  - Development commands and workflows

- **Difficulty Levels**: Easy, Medium, Hard
- **Progress Tracking**: Visual progress bar and statistics
- **Keyboard Shortcuts**:
  - **Space or Right Arrow**: Flip card / Mark correct
  - **Left Arrow**: Previous card
  - Click card to flip

- **Interactive Controls**:
  - Reveal/Hide answers
  - Navigate between cards
  - Skip cards
  - Reset and retry

## 📚 Flashcard Topics

### Fundamentals (Easy)
- What is EventTracker?
- Core technologies (FastAPI, SQLite, Jinja2)
- Main components (main.py, services)
- Configuration and environment variables
- Development commands

### Architecture (Medium)
- Service layer breakdown
- Testing frameworks and strategies
- Entry sorting (sort_key format)
- Story Mode and AI features
- Package management with uv

### Advanced (Hard)
- CSRF protection mechanism
- Database approach (no ORM/migrations)
- FTS5 and embeddings scope
- Type checking with Pyright
- E2E test isolation strategy

## 🎓 How to Use

1. **Review Mode**: Read questions and answers at your own pace
2. **Test Mode**: Try to answer before flipping the card
3. **Track Progress**: Monitor your correct answers and skip count
4. **Reset**: Start over whenever you want

## 🎨 Design

The flashcard application features:
- Clean, modern interface with gradient background
- Smooth card flip animations
- Visual difficulty badges
- Progress tracking with stats
- Responsive design for all screen sizes
- Keyboard navigation support

## 💡 Tips

- Start with Easy cards to understand the basics
- Progress to Medium for architecture knowledge
- Challenge yourself with Hard cards for deep understanding
- Use keyboard shortcuts for faster navigation
- Revisit the codebase for questions you struggle with
