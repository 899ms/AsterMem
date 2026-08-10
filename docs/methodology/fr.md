# Comment fonctionne AsterMem

La plupart des produits de « mémoire IA » engloutissent vos mots dans une boîte noire — vous ne savez jamais ce qui a été retenu, ni pourquoi, ni quand cela refera surface. AsterMem prend un autre chemin : **votre mémoire est d'abord votre patrimoine, et seulement ensuite du contexte pour l'IA.** Ce document explique chacune des décisions de conception derrière ce cadre.

## 1. Le texte original est la seule vérité

Chaque mémoire est stockée en Markdown brut. Tout ce que l'IA génère — résumés, étiquettes, votre profil — n'est qu'un **dérivé**, reconstructible à tout moment à partir de la source.

Ce n'est pas du purisme. C'est un garde-fou contre une voie de dégradation fatale : **les paraphrases de paraphrases**. Un résumé est une compression avec perte ; si le système résume sans cesse ses propres résumés, chaque passe s'éloigne un peu plus de ce que vous avez réellement écrit — comme une photocopie de photocopie dont les lettres finissent par se brouiller. AsterMem impose donc une contrainte stricte : **tout appel d'IA qui produit ou réécrit une conclusion doit recevoir le texte original en entrée.** Les artefacts intermédiaires ne servent que de référence.

Vous pouvez modifier les fichiers MD avec n'importe quel éditeur, l'index se synchronise automatiquement. Vos données ne sont jamais enfermées dans une base de données : exporter, c'est copier un dossier.

## 2. Une récupération à deux niveaux : documents et passages

Dans une longue matière mémorielle, seuls un ou deux paragraphes sont généralement pertinents pour la question du moment. AsterMem découpe automatiquement chaque mémoire en **passages (trunks)**, chacun doté de son propre résumé, de ses étiquettes et de son embedding. Au moment de la requête :

- **La recherche par mots-clés** (index plein texte Whoosh) gère les correspondances exactes : noms, projets, jargon
- **La recherche sémantique** (vecteurs) gère l'intention floue : « qu'avais-je dit de surveiller ? »
- **Le mode hybride** fusionne les deux par RRF (Reciprocal Rank Fusion), avec une pondération dynamique selon les caractéristiques de la requête

L'IA reçoit des résultats à la précision du passage, pas des documents entiers. Les fenêtres de contexte sont une ressource rare — 500 mots pertinents valent mieux que 5 000 hors sujet.

## 3. La récupération est une navigation, pas une question-réponse

Chaque recherche renvoie plus que des résultats : elle renvoie un **guidage vers l'étape suivante** — identifiants de mémoires sémantiquement proches qui n'ont pas été affichées, étiquettes présentes dans les résultats, documents qui méritent d'être dépliés. L'IA n'a pas à deviner sa prochaine requête ; elle suit les liens intrinsèques de votre graphe de mémoire.

C'est ainsi que les humains remontent le fil de leurs souvenirs : on ne s'arrête pas au premier résultat — on poursuit vers « cette chose que la source mentionnait ».

## 4. Le profil : « qui est cette personne » en un seul appel

Obliger l'IA à réapprendre qui vous êtes à chaque session, c'est le gaspillage fondamental du chat sans état. La couche profil de AsterMem distille l'ensemble de votre mémoire en un contexte dense qu'un agent récupère par un unique appel `get_profile`.

Le profil repose sur trois couches sources :

1. **Les informations de base** — des champs structurés comme le nom, la profession, le fuseau horaire. L'IA les remplit automatiquement à partir de vos mémoires ; vous pouvez tout modifier, et **dès que vous éditez un champ, l'IA n'y touche plus jamais**. Chaque changement archive l'ancienne valeur dans l'historique des versions.
2. **Votre propre présentation** — du Markdown écrit par vous, transmis mot pour mot à l'IA. Aucun chemin de code du système ne peut le modifier.
3. **Ce que l'IA sait** — des observations distillées de vos mémoires, réparties en traits de long terme, activité récente et vue d'ensemble des sujets.

## 5. Chaque phrase écrite par l'IA est traçable

Toute conclusion que l'IA inscrit dans votre profil doit citer les identifiants de ses mémoires sources. **Les affirmations introuvables sont écartées dès la couche d'analyse** — non pas relues puis supprimées, mais jamais admises.

La génération et la vérification sont deux appels d'IA indépendants : d'abord distiller des conclusions candidates, puis un auditeur confronte chacune au texte original — « la source étaye-t-elle vraiment cette affirmation ? ». Une rétrospection quotidienne passe aussi en revue les conclusions existantes : les sources supprimées sont signalées « source invalide », celles restées longtemps sans vérification « peut-être obsolètes », et tout atterrit dans une liste en attente de votre jugement. **Le système ne supprime jamais en silence, et ne croit jamais en silence.**

## 6. Le rêve : une consolidation profonde à basse fréquence

La distillation quotidienne ne voit que l'incrément du jour ; elle ne peut pas repérer les motifs qui s'étendent sur des mois. AsterMem emprunte l'idée du « rêve » (consolidation hors ligne) proposée par des chercheurs d'Anthropic : réexaminer périodiquement l'ensemble de la mémoire — dédoublonner, fusionner, résoudre les contradictions, dégager les thèmes de long terme.

Le choix de conception décisif : **la consolidation profonde ne prend jamais effet directement.** Elle produit une version candidate ; vous examinez le comparatif (ce qui a été ajouté, fusionné, retiré) puis vous l'adoptez ou la rejetez vous-même. La consolidation est déclenchée par les événements — assez de contenu nouveau accumulé, points en attente qui s'entassent, import massif terminé — et non par un cron rigide. On ne fait pas le grand ménage à date fixe ; on le fait quand le désordre se voit.

La consolidation profonde a aussi un compagnon léger au quotidien : **le rangement à l'écriture**. À chaque nouvelle mémoire, le système la met en balance avec les mémoires similaires existantes — une décision dépassée est remplacée, un fait déjà consigné n'est pas stocké deux fois. Le rangement ne fait qu'archiver, jamais supprimer ; chaque décision est consignée avec son raisonnement dans le journal d'entretien, et tout ce qui a été archivé se restaure en un clic. Dans le doute, tout est conservé. Et si vous préférez une bibliothèque sans intervention, les résultats du rêve peuvent s'appliquer automatiquement — mais seulement quand chaque conclusion passe l'audit ; le moindre point douteux attend toujours votre décision.

## 7. Visible, modifiable, désactivable

Un profil est le résumé que l'IA fait de vous — peut-être faux, peut-être partial. Le produit doit donc garantir trois choses :

- **Toujours visible** — « ce que voient les agents » est affiché mot pour mot ; il n'existe aucun prompt caché
- **Toujours modifiable** — chaque conclusion peut être conservée ou supprimée, chaque champ réécrit
- **Toujours désactivable** — le profil est désactivé par défaut ; éteint, il ne déclenche aucun appel d'IA et ne coûte rien

La confiance ne se bâtit pas sur des promesses. Elle se bâtit sur « vous pouvez ouvrir et vérifier à tout moment, et corriger en un clic ».

## 8. Conçu pour les agents

AsterMem n'est pas un outil documentaire classique : c'est un **backend de mémoire pour agents** :

- Une API d'outils complète (recherche, lecture/écriture, profil) avec authentification par jeton Bearer et paliers de permissions lecture/écriture/destruction
- Un paquet Skill prêt à l'emploi : Cursor, Claude Code et les autres agents l'installent et démarrent
- `quick_match` renvoie en un seul appel le contexte temporel, les passages les plus pertinents et le guidage vers l'étape suivante — pensé pour l'ouverture de session
- `capture_conversation` permet à l'agent de confier une conversation entière : le texte est conservé mot pour mot, et ce qui mérite d'être retenu est distillé en arrière-plan en mémoires indépendantes, chacune reliée à l'original — la sauvegarde ne dépend plus de la mémoire de l'agent
- Les réponses de recherche obéissent à un budget de caractères et à une limite de temps stricte : quelle que soit la taille de la bibliothèque, elle ne bloque jamais le tour de l'agent

Vous fournissez la matière. L'IA se souvient de qui vous êtes. C'est AsterMem.
