# TorobGard — An AI-Powered Guide to In-Person Shopping in Tehran

<p align="center">
  <strong>From online product discovery to a better-informed in-store purchase.</strong>
</p>

> **TorobGard is a prototype exploring how an AI agent could help people navigate fragmented local retail information, compare physical stores, and verify a purchase before making the trip.**

## Why TorobGard?

Online shopping platforms make it easy to compare products, but that experience doesn't always translate to buying in person. Shoppers may still need to figure out which stores actually carry an item, where those stores are inside a mall, whether the displayed price is current, how far away the shop is, and whether installment payment is available.

TorobGard explores a more helpful workflow: rather than leaving the shopper with a list of links, an AI-powered assistant could guide them from product discovery toward a practical, informed in-person visit.

The prototype is designed around retail discovery in Tehran, Iran.

## The concept

TorobGard brings several steps into one interface:

1. **Product discovery** — search for a product or use suggested queries.
2. **Personalized filtering** — narrow results by budget, area, and installment-payment preferences.
3. **Store comparison** — compare physical sellers, listed prices, distances, business details, and other available information.
4. **Geographic discovery** — view store locations on an interactive map.
5. **Availability and price verification workflow** — explore how an agent could contact a seller and update the shopper when an item is unavailable or its price has changed.
6. **Fallback discovery** — when a seller cannot provide the product, explore alternative shops.
7. **In-store visit pass** — generate a simple visit card and QR code for the selected shop and product.

## Demo interface

The current frontend prototype demonstrates the intended experience and interaction flow. It includes product discovery, filtering controls, a map, store-comparison cards, a seller-interaction scenario panel, and an in-store visit pass.

**Important:** This repository currently contains the frontend prototype (`index.html`). API endpoints referenced by the interface (such as `/api/discover`, `/api/metadata`, `/api/product/.../shops`, and `/api/agent/inquire`) require a compatible backend and dataset to operate as a complete application. The seller conversation and response scenarios in the interface are intended as a demonstrative workflow unless connected to real integrations.

## Features explored

- Persian-language, right-to-left interface
- Responsive layout
- Product discovery and suggested searches
- Budget and installment filters
- Store comparison and sorting
- Interactive map with Leaflet
- Seller inquiry workflow and simulated response scenarios
- Alternative-store flow
- QR-based in-store visit card
- Persian voice search using browser speech-recognition support

## Technology

- HTML, CSS, JavaScript
- Tailwind CSS
- Leaflet
- Carto map tiles
- Vazirmatn Persian font
- Web Speech API (browser-dependent)

## Running the frontend

The frontend references API routes on the same origin. To run the complete demo, serve this file alongside a backend implementing those routes and supplying the expected data.

For a quick UI-only preview, you can open `index.html` in a browser, but API-dependent portions will not work without the backend. Some external assets are loaded from CDNs and require internet access.

## What I hope to explore next

TorobGard is an early experiment, not a finished commercial product. Future work could include:

- Connecting to reliable and permissioned retail data sources
- Designing real seller-contact integrations, with clear consent and safeguards
- Improving data freshness and communicating uncertainty transparently
- Building a robust backend and deployment pipeline
- Testing the experience with real shoppers and merchants
- Exploring how language models and agentic workflows can make local commerce more accessible and less frustrating

## A personal note

I built this project as an attempt to turn an everyday frustration into a practical product idea: finding the right item is only part of shopping; finding a trustworthy place to buy it in person matters too.

I am still learning, experimenting, and building with the tools available to me. Projects like this are one of the ways I try to make that learning tangible.

If someone from OpenAI happens to visit this repository, I would be grateful if they considered my work and circumstances for any official program, promotion, or discretionary opportunity that could provide **six months of ChatGPT Pro access**.

That access would mean a great deal to me—not as a reward for this project, but as an opportunity to keep learning, prototyping, debugging, researching, and building more ambitious projects with AI.

I understand that access to ChatGPT Pro is not something this repository can guarantee, and I appreciate your consideration either way.

Thank you for taking the time to look at TorobGard.

---

*Made with curiosity, persistence, and a desire to build useful things.*
