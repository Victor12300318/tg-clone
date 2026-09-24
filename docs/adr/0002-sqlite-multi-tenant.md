# SQLite multi-tenant em vez de Postgres

Um SaaS multi-tenant sobre SQLite único (`data/cloner.db`) com `user_id` em toda tabela, em vez de migrar para Postgres antes do primeiro cliente pagante. Motivo: os engines já são singletons asyncio num único processo FastAPI e o Pyrogram aguenta muitas sessões nesse processo — o gargalo real (escrita concorrente) só aparece com milhares de usuários ativos. A fuga documentada: WAL ativado, schema já nascedo com `user_id`, migração para Postgres é dump-and-load quando a concorrência de escrita doer de verdade. Não troque por "porque SaaS usa Postgres" sem medir.

## Considered Options

- **Postgres desde o início** — rejeitado: infra gerenciada, custo e migração antes de validação de mercado.
- **SQLite por usuário** — rejeitado: quebra foreign keys globais, complica backup e contabilidade, multiplica conexões.
- **SQLite único + `user_id` (escolhido)** — zero infra nova, isolamento garantido por filtro de dono em toda query.
