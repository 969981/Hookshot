using System.Globalization;

namespace HookshotBuilder;

/// One of the game's languages: its code in language.txt, its own name, and the app's text in it.
sealed record Language(string Code, string Name, string[] Text)
{
    public override string ToString() => Name;
}

enum S
{
    Subtitle, GameFiles, Nup, Folders, DropHint, SaveTo, Build, Browse, Needs, Free, Checking, VersionOk, VersionBad, ToolsMissing, PythonMissing,
    Building, Patience, Elapsed, Read, Lift, Compile, Finish, ShowDetails, HideDetails, Cancel, Back, OpenFolder, Play, Ready, BuiltIn,
    Stopped, Cancelled, CancelledNote, CloseQuestion, LanguageLabel,
    // (the game library: every game, its state, Install, Update, Update all and its summary)
    LibTitle, LibMissing, LibCurrent, LibUpdate, LibInstalled, LibAlsoIn, LibTaken, LibInstall, LibUpdateOne, LibUpdateAll, LibPickFile,
    LibPickFolder, LibNoFolders, LibAsk, LibNothing, LibUpdating, LibUpdatingOne, LibAllUpdated, LibSomeFailed,
    ResUpdated, ResCurrent, ResWould, ResWouldPlain, ResMissing, ResFailed, LibRebuild, ResRebuild, LibSub,
    LibStopped, LibStoppedSub, ResStopped,
    // (the update, English only for now: Strings.Text falls back to it)
    Download, Updating, UpdateFailed,
}

static class Strings
{
    static readonly string[] En =
    {
        "Turns your own copy of Ocarina of Time into a game folder you can start on PC.", "Game files", ".nup file", "romfs and exefs",
        "You can also drop a file or folder onto this window.", "Save the game to", "Build", "Browse", "Takes about 45 minutes and needs 45 GB.",
        "{0} has {1} GB free.", "Checking the game version...", "This is the version Hookshot supports.", "This version isn't supported. Hookshot needs build e0f3ccfa.",
        "The Hookshot tools were not found next to this app.", "Python was not found.",
        "Building", "This takes a while, and your PC stays usable.", "Elapsed", "Reading your game files", "Translating the game to PC code", "Compiling",
        "Putting the game folder together", "Show details", "Hide details", "Cancel", "Back", "Open folder", "Play", "Your game is ready", "built in",
        "The build stopped", "Build cancelled", "You can start again from the same folder.", "Stop the build and close?", "Language",
        "Your games", "Not installed", "Up to date · {0}", "Update available · yours is {0}", "Installed", "Also installed in {0}",
        "Its folder {0} holds {1}: installing it there would replace {1}. Move that game, or choose another folder for your games.", "Install",
        "Update", "Update all", "Choose your copy of {0} (.nup)", "Choose the folder with the romfs and exefs of {0}",
        "That folder has no romfs and exefs folders in it.", "Update these games now? Your saves stay.", "Every installed game is up to date.",
        "Updating your games", "Updating {0} ({1} of {2})", "Your games are updated", "Not every game could be updated", "{0}: updated to {1}",
        "{0}: up to date ({1})", "{0}: will be updated from {1}", "{0}: will be updated", "{0}: not installed", "{0}: failed: {1}",
        "Built before updates came in: choose this game's files once to update it", "{0}: choose this game's files once to update it",
        "Install a game from your own copy, or update the ones you have.",
        "Update all stopped",
        "The games updated before it stopped are updated; the others are as they were and still play.",
        "{0}: not updated: it stopped before this game",
        "Downloading the new version", "Updating your game", "The update could not be downloaded",
    };
    static readonly string[] Fr =
    {
        "Transforme votre copie d'Ocarina of Time en un dossier de jeu à lancer sur PC.", "Fichiers du jeu", "Fichier .nup", "romfs et exefs",
        "Vous pouvez aussi déposer un fichier ou un dossier sur cette fenêtre.", "Enregistrer le jeu dans", "Créer", "Parcourir", "Prend environ 45 minutes et nécessite 45 Go.",
        "{0} : {1} Go libres.", "Vérification de la version du jeu...", "Cette version est prise en charge par Hookshot.", "Cette version n'est pas prise en charge. Hookshot a besoin de la version e0f3ccfa.",
        "Les outils Hookshot sont introuvables à côté de cette application.", "Python est introuvable.",
        "Création en cours", "Cela prend du temps, et votre PC reste utilisable.", "Temps écoulé", "Lecture de vos fichiers de jeu", "Traduction du jeu en code PC", "Compilation",
        "Assemblage du dossier du jeu", "Afficher les détails", "Masquer les détails", "Annuler", "Retour", "Ouvrir le dossier", "Jouer", "Votre jeu est prêt", "créé en",
        "La création s'est arrêtée", "Création annulée", "Vous pouvez recommencer depuis le même dossier.", "Arrêter la création et fermer ?", "Langue",
        "Vos jeux", "Non installé", "À jour · {0}", "Mise à jour disponible · le vôtre est en {0}", "Installé", "Aussi installé dans {0}",
        "Son dossier {0} contient {1} : l'installer là remplacerait {1}. Déplacez ce jeu, ou choisissez un autre dossier pour vos jeux.", "Installer",
        "Mettre à jour", "Tout mettre à jour", "Choisissez votre copie de {0} (.nup)", "Choisissez le dossier avec le romfs et l'exefs de {0}",
        "Ce dossier ne contient pas de dossiers romfs et exefs.", "Mettre ces jeux à jour maintenant ? Vos sauvegardes sont conservées.",
        "Tous les jeux installés sont à jour.", "Mise à jour de vos jeux", "Mise à jour de {0} ({1} sur {2})", "Vos jeux sont à jour",
        "Certains jeux n'ont pas pu être mis à jour", "{0} : mis à jour en {1}", "{0} : à jour ({1})", "{0} : sera mis à jour depuis {1}",
        "{0} : sera mis à jour", "{0} : non installé", "{0} : échec : {1}",
        "Créé avant les mises à jour : choisissez une fois les fichiers de ce jeu pour le mettre à jour", "{0} : choisissez une fois les fichiers de ce jeu pour le mettre à jour",
        "Installez un jeu depuis votre propre copie, ou mettez à jour ceux que vous avez.",
        "La mise à jour de tous les jeux s'est arrêtée",
        "Les jeux mis à jour avant l'arrêt le sont ; les autres sont restés tels quels et se lancent toujours.",
        "{0} : non mis à jour : arrêt avant ce jeu",
    };
    static readonly string[] De =
    {
        "Macht aus deiner eigenen Kopie von Ocarina of Time einen Spielordner für den PC.", "Spieldateien", ".nup-Datei", "romfs und exefs",
        "Du kannst auch eine Datei oder einen Ordner auf dieses Fenster ziehen.", "Spiel speichern unter", "Erstellen", "Durchsuchen", "Dauert etwa 45 Minuten und benötigt 45 GB.",
        "{0} hat {1} GB frei.", "Spielversion wird geprüft...", "Diese Version wird von Hookshot unterstützt.", "Diese Version wird nicht unterstützt. Hookshot benötigt Build e0f3ccfa.",
        "Die Hookshot-Werkzeuge wurden neben dieser App nicht gefunden.", "Python wurde nicht gefunden.",
        "Wird erstellt", "Das dauert eine Weile, dein PC bleibt nutzbar.", "Vergangen", "Spieldateien werden gelesen", "Spiel wird in PC-Code übersetzt", "Kompilieren",
        "Spielordner wird zusammengestellt", "Details anzeigen", "Details ausblenden", "Abbrechen", "Zurück", "Ordner öffnen", "Spielen", "Dein Spiel ist fertig", "erstellt in",
        "Die Erstellung wurde angehalten", "Erstellung abgebrochen", "Du kannst im selben Ordner neu beginnen.", "Erstellung beenden und schließen?", "Sprache",
        "Deine Spiele", "Nicht installiert", "Aktuell · {0}", "Update verfügbar · deins ist {0}", "Installiert", "Auch installiert in {0}",
        "Sein Ordner {0} enthält {1}: dort installiert, würde es {1} ersetzen. Verschiebe dieses Spiel oder wähle einen anderen Ordner für deine Spiele.",
        "Installieren", "Aktualisieren", "Alle aktualisieren", "Wähle deine Kopie von {0} (.nup)", "Wähle den Ordner mit romfs und exefs von {0}",
        "Dieser Ordner enthält keine Ordner romfs und exefs.", "Diese Spiele jetzt aktualisieren? Deine Spielstände bleiben.",
        "Alle installierten Spiele sind aktuell.", "Deine Spiele werden aktualisiert", "{0} wird aktualisiert ({1} von {2})",
        "Deine Spiele sind aktualisiert", "Nicht alle Spiele konnten aktualisiert werden", "{0}: aktualisiert auf {1}", "{0}: aktuell ({1})",
        "{0}: wird aktualisiert von {1}", "{0}: wird aktualisiert", "{0}: nicht installiert", "{0}: fehlgeschlagen: {1}",
        "Vor den Updates erstellt: wähle einmal die Dateien dieses Spiels, um es zu aktualisieren", "{0}: wähle einmal die Dateien dieses Spiels, um es zu aktualisieren",
        "Installiere ein Spiel aus deiner eigenen Kopie oder aktualisiere die, die du hast.",
        "Die Aktualisierung aller Spiele wurde angehalten",
        "Die Spiele, die vorher aktualisiert wurden, sind aktuell; die anderen sind unverändert und lassen sich weiter spielen.",
        "{0}: nicht aktualisiert: vor diesem Spiel angehalten",
    };
    static readonly string[] It =
    {
        "Trasforma la tua copia di Ocarina of Time in una cartella di gioco da avviare su PC.", "File di gioco", "File .nup", "romfs ed exefs",
        "Puoi anche trascinare un file o una cartella su questa finestra.", "Salva il gioco in", "Crea", "Sfoglia", "Richiede circa 45 minuti e 45 GB.",
        "{0} ha {1} GB liberi.", "Controllo della versione del gioco...", "Questa versione è supportata da Hookshot.", "Questa versione non è supportata. Hookshot richiede la build e0f3ccfa.",
        "Gli strumenti di Hookshot non sono stati trovati accanto a questa app.", "Python non è stato trovato.",
        "Creazione in corso", "Ci vuole un po', e il PC resta utilizzabile.", "Trascorso", "Lettura dei file di gioco", "Traduzione del gioco in codice PC", "Compilazione",
        "Assemblaggio della cartella di gioco", "Mostra dettagli", "Nascondi dettagli", "Annulla", "Indietro", "Apri cartella", "Gioca", "Il tuo gioco è pronto", "creato in",
        "La creazione si è interrotta", "Creazione annullata", "Puoi ricominciare dalla stessa cartella.", "Interrompere la creazione e chiudere?", "Lingua",
        "I tuoi giochi", "Non installato", "Aggiornato · {0}", "Aggiornamento disponibile · il tuo è {0}", "Installato", "Installato anche in {0}",
        "La sua cartella {0} contiene {1}: installarlo lì sostituirebbe {1}. Sposta quel gioco o scegli un'altra cartella per i tuoi giochi.",
        "Installa", "Aggiorna", "Aggiorna tutto", "Scegli la tua copia di {0} (.nup)", "Scegli la cartella con romfs ed exefs di {0}",
        "Quella cartella non contiene le cartelle romfs ed exefs.", "Aggiornare ora questi giochi? I salvataggi restano.",
        "Tutti i giochi installati sono aggiornati.", "Aggiornamento dei tuoi giochi", "Aggiornamento di {0} ({1} di {2})",
        "I tuoi giochi sono aggiornati", "Non è stato possibile aggiornare tutti i giochi", "{0}: aggiornato a {1}", "{0}: aggiornato ({1})",
        "{0}: verrà aggiornato da {1}", "{0}: verrà aggiornato", "{0}: non installato", "{0}: non riuscito: {1}",
        "Creato prima degli aggiornamenti: scegli una volta i file di questo gioco per aggiornarlo", "{0}: scegli una volta i file di questo gioco per aggiornarlo",
        "Installa un gioco dalla tua copia o aggiorna quelli che hai.",
        "L'aggiornamento di tutti i giochi si è interrotto",
        "I giochi aggiornati prima dell'interruzione sono aggiornati; gli altri sono rimasti com'erano e funzionano ancora.",
        "{0}: non aggiornato: interrotto prima di questo gioco",
    };
    static readonly string[] Es =
    {
        "Convierte tu copia de Ocarina of Time en una carpeta de juego para iniciar en PC.", "Archivos del juego", "Archivo .nup", "romfs y exefs",
        "También puedes soltar un archivo o una carpeta en esta ventana.", "Guardar el juego en", "Crear", "Examinar", "Tarda unos 45 minutos y necesita 45 GB.",
        "{0} tiene {1} GB libres.", "Comprobando la versión del juego...", "Hookshot admite esta versión.", "Esta versión no es compatible. Hookshot necesita la build e0f3ccfa.",
        "No se encontraron las herramientas de Hookshot junto a esta aplicación.", "No se encontró Python.",
        "Creando", "Tarda un rato, y tu PC sigue siendo utilizable.", "Transcurrido", "Leyendo tus archivos del juego", "Traduciendo el juego a código de PC", "Compilando",
        "Preparando la carpeta del juego", "Mostrar detalles", "Ocultar detalles", "Cancelar", "Atrás", "Abrir carpeta", "Jugar", "Tu juego está listo", "creado en",
        "La creación se detuvo", "Creación cancelada", "Puedes empezar de nuevo desde la misma carpeta.", "¿Detener la creación y cerrar?", "Idioma",
        "Tus juegos", "No instalado", "Actualizado · {0}", "Actualización disponible · el tuyo es {0}", "Instalado", "También instalado en {0}",
        "Su carpeta {0} contiene {1}: instalarlo ahí reemplazaría {1}. Mueve ese juego o elige otra carpeta para tus juegos.", "Instalar",
        "Actualizar", "Actualizar todo", "Elige tu copia de {0} (.nup)", "Elige la carpeta con el romfs y el exefs de {0}",
        "Esa carpeta no tiene carpetas romfs y exefs.", "¿Actualizar estos juegos ahora? Tus partidas guardadas se conservan.",
        "Todos los juegos instalados están actualizados.", "Actualizando tus juegos", "Actualizando {0} ({1} de {2})",
        "Tus juegos están actualizados", "No se pudieron actualizar todos los juegos", "{0}: actualizado a {1}", "{0}: actualizado ({1})",
        "{0}: se actualizará desde {1}", "{0}: se actualizará", "{0}: no instalado", "{0}: falló: {1}",
        "Creado antes de las actualizaciones: elige una vez los archivos de este juego para actualizarlo", "{0}: elige una vez los archivos de este juego para actualizarlo",
        "Instala un juego desde tu propia copia o actualiza los que tienes.",
        "La actualización de todos los juegos se detuvo",
        "Los juegos actualizados antes de detenerse están actualizados; los demás siguen como estaban y se pueden jugar.",
        "{0}: no actualizado: se detuvo antes de este juego",
    };
    static readonly string[] Pt =
    {
        "Transforma sua cópia de Ocarina of Time em uma pasta de jogo para iniciar no PC.", "Arquivos do jogo", "Arquivo .nup", "romfs e exefs",
        "Você também pode soltar um arquivo ou uma pasta nesta janela.", "Salvar o jogo em", "Criar", "Procurar", "Leva cerca de 45 minutos e precisa de 45 GB.",
        "{0} tem {1} GB livres.", "Verificando a versão do jogo...", "Esta versão é compatível com o Hookshot.", "Esta versão não é compatível. O Hookshot precisa da build e0f3ccfa.",
        "As ferramentas do Hookshot não foram encontradas ao lado deste aplicativo.", "O Python não foi encontrado.",
        "Criando", "Isso leva um tempo, e seu PC continua utilizável.", "Decorrido", "Lendo seus arquivos do jogo", "Traduzindo o jogo para código de PC", "Compilando",
        "Montando a pasta do jogo", "Mostrar detalhes", "Ocultar detalhes", "Cancelar", "Voltar", "Abrir pasta", "Jogar", "Seu jogo está pronto", "criado em",
        "A criação parou", "Criação cancelada", "Você pode recomeçar a partir da mesma pasta.", "Parar a criação e fechar?", "Idioma",
        "Seus jogos", "Não instalado", "Atualizado · {0}", "Atualização disponível · o seu é {0}", "Instalado", "Também instalado em {0}",
        "A pasta dele, {0}, contém {1}: instalá-lo ali substituiria {1}. Mova esse jogo ou escolha outra pasta para seus jogos.", "Instalar",
        "Atualizar", "Atualizar tudo", "Escolha sua cópia de {0} (.nup)", "Escolha a pasta com o romfs e o exefs de {0}",
        "Essa pasta não tem as pastas romfs e exefs.", "Atualizar estes jogos agora? Seus saves continuam.",
        "Todos os jogos instalados estão atualizados.", "Atualizando seus jogos", "Atualizando {0} ({1} de {2})", "Seus jogos estão atualizados",
        "Nem todos os jogos puderam ser atualizados", "{0}: atualizado para {1}", "{0}: atualizado ({1})", "{0}: será atualizado de {1}",
        "{0}: será atualizado", "{0}: não instalado", "{0}: falhou: {1}",
        "Criado antes das atualizações: escolha uma vez os arquivos deste jogo para atualizá-lo", "{0}: escolha uma vez os arquivos deste jogo para atualizá-lo",
        "Instale um jogo a partir da sua própria cópia ou atualize os que você tem.",
        "A atualização de todos os jogos parou",
        "Os jogos atualizados antes da parada estão atualizados; os outros continuam como estavam e ainda funcionam.",
        "{0}: não atualizado: parou antes deste jogo",
    };
    static readonly string[] Nl =
    {
        "Maakt van je eigen exemplaar van Ocarina of Time een spelmap om op pc te starten.", "Spelbestanden", ".nup-bestand", "romfs en exefs",
        "Je kunt ook een bestand of map op dit venster slepen.", "Spel opslaan in", "Maken", "Bladeren", "Duurt ongeveer 45 minuten en vereist 45 GB.",
        "{0} heeft {1} GB vrij.", "Spelversie controleren...", "Deze versie wordt door Hookshot ondersteund.", "Deze versie wordt niet ondersteund. Hookshot heeft build e0f3ccfa nodig.",
        "De Hookshot-tools zijn niet naast deze app gevonden.", "Python is niet gevonden.",
        "Bezig met maken", "Dit duurt even, en je pc blijft bruikbaar.", "Verstreken", "Je spelbestanden lezen", "Spel vertalen naar pc-code", "Compileren",
        "Spelmap samenstellen", "Details tonen", "Details verbergen", "Annuleren", "Terug", "Map openen", "Spelen", "Je spel is klaar", "gemaakt in",
        "Het maken is gestopt", "Maken geannuleerd", "Je kunt opnieuw beginnen vanuit dezelfde map.", "Maken stoppen en sluiten?", "Taal",
        "Je spellen", "Niet geïnstalleerd", "Bijgewerkt · {0}", "Update beschikbaar · de jouwe is {0}", "Geïnstalleerd", "Ook geïnstalleerd in {0}",
        "De map {0} bevat {1}: daar installeren zou {1} vervangen. Verplaats dat spel, of kies een andere map voor je spellen.", "Installeren",
        "Bijwerken", "Alles bijwerken", "Kies je exemplaar van {0} (.nup)", "Kies de map met de romfs en exefs van {0}",
        "Die map bevat geen mappen romfs en exefs.", "Deze spellen nu bijwerken? Je opgeslagen spellen blijven.",
        "Alle geïnstalleerde spellen zijn bijgewerkt.", "Je spellen bijwerken", "{0} bijwerken ({1} van {2})", "Je spellen zijn bijgewerkt",
        "Niet alle spellen konden worden bijgewerkt", "{0}: bijgewerkt naar {1}", "{0}: bijgewerkt ({1})", "{0}: wordt bijgewerkt vanaf {1}",
        "{0}: wordt bijgewerkt", "{0}: niet geïnstalleerd", "{0}: mislukt: {1}",
        "Gemaakt vóór de updates: kies één keer de bestanden van dit spel om het bij te werken", "{0}: kies één keer de bestanden van dit spel om het bij te werken",
        "Installeer een spel vanaf je eigen exemplaar, of werk de spellen bij die je hebt.",
        "Alles bijwerken is gestopt",
        "De spellen die eerder zijn bijgewerkt, zijn bijgewerkt; de andere zijn zoals ze waren en blijven speelbaar.",
        "{0}: niet bijgewerkt: gestopt vóór dit spel",
    };
    static readonly string[] Pl =
    {
        "Zamienia Twoją kopię Ocarina of Time w folder gry, który uruchomisz na PC.", "Pliki gry", "Plik .nup", "romfs i exefs",
        "Możesz też upuścić plik lub folder na to okno.", "Zapisz grę w", "Utwórz", "Przeglądaj", "Trwa około 45 minut i wymaga 45 GB.",
        "{0}: wolne {1} GB.", "Sprawdzanie wersji gry...", "Ta wersja jest obsługiwana przez Hookshot.", "Ta wersja nie jest obsługiwana. Hookshot wymaga kompilacji e0f3ccfa.",
        "Nie znaleziono narzędzi Hookshot obok tej aplikacji.", "Nie znaleziono Pythona.",
        "Tworzenie", "To chwilę potrwa, a komputer pozostanie w użyciu.", "Upłynęło", "Odczytywanie plików gry", "Tłumaczenie gry na kod PC", "Kompilowanie",
        "Składanie folderu gry", "Pokaż szczegóły", "Ukryj szczegóły", "Anuluj", "Wstecz", "Otwórz folder", "Graj", "Twoja gra jest gotowa", "utworzono w",
        "Tworzenie zostało przerwane", "Anulowano tworzenie", "Możesz zacząć ponownie z tego samego folderu.", "Przerwać tworzenie i zamknąć?", "Język",
        "Twoje gry", "Nie zainstalowano", "Aktualna · {0}", "Dostępna aktualizacja · Twoja to {0}", "Zainstalowano", "Zainstalowana także w {0}",
        "Jej folder {0} zawiera {1}: instalacja tam zastąpiłaby {1}. Przenieś tę grę albo wybierz inny folder na swoje gry.", "Zainstaluj",
        "Aktualizuj", "Aktualizuj wszystko", "Wybierz swoją kopię {0} (.nup)", "Wybierz folder z romfs i exefs gry {0}",
        "Ten folder nie zawiera folderów romfs i exefs.", "Zaktualizować te gry teraz? Zapisy gry zostaną.",
        "Wszystkie zainstalowane gry są aktualne.", "Aktualizowanie gier", "Aktualizowanie {0} ({1} z {2})", "Twoje gry są zaktualizowane",
        "Nie udało się zaktualizować wszystkich gier", "{0}: zaktualizowano do {1}", "{0}: aktualna ({1})", "{0}: zostanie zaktualizowana z {1}",
        "{0}: zostanie zaktualizowana", "{0}: nie zainstalowano", "{0}: niepowodzenie: {1}",
        "Utworzona przed aktualizacjami: wybierz raz pliki tej gry, aby ją zaktualizować", "{0}: wybierz raz pliki tej gry, aby ją zaktualizować",
        "Zainstaluj grę z własnej kopii albo zaktualizuj te, które masz.",
        "Aktualizacja wszystkich gier została przerwana",
        "Gry zaktualizowane przed przerwaniem są zaktualizowane; pozostałe są takie jak były i nadal działają.",
        "{0}: nie zaktualizowano: przerwano przed tą grą",
    };
    static readonly string[] Ru =
    {
        "Превращает вашу копию Ocarina of Time в папку с игрой для запуска на ПК.", "Файлы игры", "Файл .nup", "romfs и exefs",
        "Также можно перетащить файл или папку в это окно.", "Сохранить игру в", "Собрать", "Обзор", "Занимает около 45 минут и требует 45 ГБ.",
        "{0}: свободно {1} ГБ.", "Проверка версии игры...", "Эта версия поддерживается Hookshot.", "Эта версия не поддерживается. Hookshot нужна сборка e0f3ccfa.",
        "Инструменты Hookshot не найдены рядом с этим приложением.", "Python не найден.",
        "Сборка", "Это займёт время, а компьютером можно пользоваться.", "Прошло", "Чтение файлов игры", "Перевод игры в код для ПК", "Компиляция",
        "Подготовка папки с игрой", "Показать подробности", "Скрыть подробности", "Отмена", "Назад", "Открыть папку", "Играть", "Ваша игра готова", "собрано за",
        "Сборка остановлена", "Сборка отменена", "Можно начать заново из той же папки.", "Остановить сборку и закрыть?", "Язык",
        "Ваши игры", "Не установлена", "Актуальна · {0}", "Доступно обновление · у вас {0}", "Установлена", "Также установлена в {0}",
        "Её папка {0} содержит {1}: установка туда заменила бы {1}. Переместите ту игру или выберите другую папку для игр.", "Установить", "Обновить",
        "Обновить все", "Выберите свою копию {0} (.nup)", "Выберите папку с romfs и exefs игры {0}", "В этой папке нет папок romfs и exefs.",
        "Обновить эти игры сейчас? Сохранения останутся.", "Все установленные игры актуальны.", "Обновление игр", "Обновление {0} ({1} из {2})",
        "Ваши игры обновлены", "Не все игры удалось обновить", "{0}: обновлена до {1}", "{0}: актуальна ({1})", "{0}: будет обновлена с {1}",
        "{0}: будет обновлена", "{0}: не установлена", "{0}: ошибка: {1}",
        "Собрана до появления обновлений: выберите файлы этой игры один раз, чтобы обновить её", "{0}: выберите файлы этой игры один раз, чтобы обновить её",
        "Установите игру из своей копии или обновите уже установленные.",
        "Обновление всех игр остановлено",
        "Игры, обновлённые до остановки, обновлены; остальные остались прежними и по-прежнему запускаются.",
        "{0}: не обновлена: остановлено до этой игры",
    };
    static readonly string[] Ja =
    {
        "お持ちの Ocarina of Time を、PC で起動できるゲームフォルダーに変換します。", "ゲームファイル", ".nup ファイル", "romfs と exefs",
        "ファイルやフォルダーをこのウィンドウにドロップすることもできます。", "ゲームの保存先", "ビルド", "参照", "約 45 分かかり、45 GB 必要です。",
        "{0} の空き容量: {1} GB", "ゲームのバージョンを確認しています...", "このバージョンは Hookshot に対応しています。", "このバージョンには対応していません。Hookshot にはビルド e0f3ccfa が必要です。",
        "このアプリの横に Hookshot ツールが見つかりません。", "Python が見つかりません。",
        "ビルド中", "時間がかかりますが、PC はそのまま使えます。", "経過時間", "ゲームファイルを読み込んでいます", "ゲームを PC 用コードに変換しています", "コンパイル中",
        "ゲームフォルダーを作成しています", "詳細を表示", "詳細を隠す", "キャンセル", "戻る", "フォルダーを開く", "プレイ", "ゲームの準備ができました", "所要時間",
        "ビルドが停止しました", "ビルドをキャンセルしました", "同じフォルダーからやり直せます。", "ビルドを中止して閉じますか?", "言語",
        "あなたのゲーム", "未インストール", "最新 · {0}", "アップデートあり · 現在 {0}", "インストール済み", "{0} にもインストールされています",
        "フォルダー {0} には {1} があります。そこにインストールすると {1} が置き換わります。そのゲームを移動するか、ゲーム用に別のフォルダーを選んでください。", "インストール", "アップデート", "すべてアップデート", "{0} のコピー (.nup) を選択",
        "{0} の romfs と exefs があるフォルダーを選択", "そのフォルダーには romfs と exefs のフォルダーがありません。", "これらのゲームを今すぐアップデートしますか? セーブデータはそのまま残ります。",
        "インストール済みのゲームはすべて最新です。", "ゲームをアップデートしています", "{0} をアップデートしています ({1}/{2})", "ゲームがアップデートされました", "一部のゲームをアップデートできませんでした", "{0}: {1} にアップデートしました",
        "{0}: 最新 ({1})", "{0}: {1} からアップデートされます", "{0}: アップデートされます", "{0}: 未インストール", "{0}: 失敗: {1}",
        "アップデート機能より前にビルドされています: アップデートするには、このゲームのファイルを一度選んでください", "{0}: アップデートするには、このゲームのファイルを一度選んでください",
        "お持ちのコピーからゲームをインストールするか、インストール済みのゲームをアップデートします。",
        "すべてのアップデートが停止しました",
        "停止前にアップデートされたゲームはアップデート済みです。その他のゲームは元のままで、引き続きプレイできます。",
        "{0}: 未アップデート: このゲームの前に停止しました",
    };
    static readonly string[] Ko =
    {
        "가지고 있는 Ocarina of Time을 PC에서 실행할 수 있는 게임 폴더로 만듭니다.", "게임 파일", ".nup 파일", "romfs 및 exefs",
        "파일이나 폴더를 이 창에 끌어다 놓을 수도 있습니다.", "게임 저장 위치", "빌드", "찾아보기", "약 45분이 걸리며 45GB가 필요합니다.",
        "{0} 여유 공간: {1}GB", "게임 버전을 확인하는 중...", "Hookshot이 지원하는 버전입니다.", "지원되지 않는 버전입니다. Hookshot에는 빌드 e0f3ccfa가 필요합니다.",
        "이 앱 옆에서 Hookshot 도구를 찾을 수 없습니다.", "Python을 찾을 수 없습니다.",
        "빌드 중", "시간이 걸리지만 PC는 계속 사용할 수 있습니다.", "경과 시간", "게임 파일을 읽는 중", "게임을 PC 코드로 변환하는 중", "컴파일 중",
        "게임 폴더를 만드는 중", "자세히 보기", "자세히 숨기기", "취소", "뒤로", "폴더 열기", "플레이", "게임이 준비되었습니다", "소요 시간",
        "빌드가 중단되었습니다", "빌드가 취소되었습니다", "같은 폴더에서 다시 시작할 수 있습니다.", "빌드를 중단하고 닫을까요?", "언어",
        "내 게임", "설치되지 않음", "최신 · {0}", "업데이트 있음 · 현재 {0}", "설치됨", "{0}에도 설치됨",
        "폴더 {0}에 {1}이(가) 있습니다. 그곳에 설치하면 {1}이(가) 대체됩니다. 그 게임을 옮기거나 게임용으로 다른 폴더를 선택하세요.", "설치", "업데이트", "모두 업데이트", "{0}의 사본(.nup)을 선택하세요",
        "{0}의 romfs와 exefs가 있는 폴더를 선택하세요", "그 폴더에는 romfs와 exefs 폴더가 없습니다.", "지금 이 게임들을 업데이트할까요? 저장 데이터는 유지됩니다.", "설치된 모든 게임이 최신입니다.", "게임을 업데이트하는 중",
        "{0} 업데이트 중 ({1}/{2})", "게임이 업데이트되었습니다", "일부 게임을 업데이트하지 못했습니다", "{0}: {1}(으)로 업데이트됨", "{0}: 최신 ({1})", "{0}: {1}에서 업데이트됩니다", "{0}: 업데이트됩니다",
        "{0}: 설치되지 않음", "{0}: 실패: {1}",
        "업데이트 기능 이전에 빌드됨: 업데이트하려면 이 게임의 파일을 한 번 선택하세요", "{0}: 업데이트하려면 이 게임의 파일을 한 번 선택하세요",
        "가지고 있는 사본으로 게임을 설치하거나, 설치된 게임을 업데이트하세요.",
        "모두 업데이트가 중단되었습니다",
        "중단 전에 업데이트된 게임은 업데이트되었습니다. 나머지 게임은 그대로이며 계속 플레이할 수 있습니다.",
        "{0}: 업데이트되지 않음: 이 게임 전에 중단됨",
    };
    static readonly string[] ZhHans =
    {
        "将你自己的 Ocarina of Time 变成可在电脑上启动的游戏文件夹。", "游戏文件", ".nup 文件", "romfs 和 exefs",
        "也可以将文件或文件夹拖放到此窗口。", "游戏保存位置", "生成", "浏览", "大约需要 45 分钟和 45 GB 空间。",
        "{0} 可用空间 {1} GB。", "正在检查游戏版本...", "Hookshot 支持此版本。", "不支持此版本。Hookshot 需要构建版本 e0f3ccfa。",
        "在此应用旁边找不到 Hookshot 工具。", "找不到 Python。",
        "正在生成", "这需要一些时间,期间电脑仍可正常使用。", "已用时间", "正在读取游戏文件", "正在将游戏转换为电脑代码", "正在编译",
        "正在整理游戏文件夹", "显示详细信息", "隐藏详细信息", "取消", "返回", "打开文件夹", "开始游戏", "你的游戏已准备就绪", "用时",
        "生成已停止", "生成已取消", "你可以从同一文件夹重新开始。", "要停止生成并关闭吗?", "语言",
        "你的游戏", "未安装", "已是最新 · {0}", "有可用更新 · 当前为 {0}", "已安装", "也安装在 {0}", "它的文件夹 {0} 中有 {1}:安装到那里会替换 {1}。请移动该游戏,或为你的游戏选择其他文件夹。", "安装", "更新", "全部更新",
        "选择你的 {0} 副本 (.nup)", "选择包含 {0} 的 romfs 和 exefs 的文件夹", "该文件夹中没有 romfs 和 exefs 文件夹。", "现在更新这些游戏吗?存档会保留。", "所有已安装的游戏都是最新的。", "正在更新你的游戏",
        "正在更新 {0} ({1}/{2})", "你的游戏已更新", "部分游戏未能更新", "{0}:已更新到 {1}", "{0}:已是最新 ({1})", "{0}:将从 {1} 更新", "{0}:将会更新", "{0}:未安装", "{0}:失败:{1}",
        "在支持更新之前生成:请选择一次此游戏的文件以更新它", "{0}:请选择一次此游戏的文件以更新它",
        "从你自己的副本安装游戏,或更新已有的游戏。",
        "全部更新已停止",
        "停止前已更新的游戏已完成更新;其他游戏保持原样,仍可游玩。",
        "{0}:未更新:在此游戏之前已停止",
    };
    static readonly string[] ZhHant =
    {
        "將你自己的 Ocarina of Time 變成可在電腦上啟動的遊戲資料夾。", "遊戲檔案", ".nup 檔案", "romfs 和 exefs",
        "也可以將檔案或資料夾拖放到此視窗。", "遊戲儲存位置", "建置", "瀏覽", "大約需要 45 分鐘和 45 GB 空間。",
        "{0} 可用空間 {1} GB。", "正在檢查遊戲版本...", "Hookshot 支援此版本。", "不支援此版本。Hookshot 需要建置版本 e0f3ccfa。",
        "在此應用程式旁找不到 Hookshot 工具。", "找不到 Python。",
        "正在建置", "這需要一些時間,期間電腦仍可正常使用。", "已用時間", "正在讀取遊戲檔案", "正在將遊戲轉換為電腦程式碼", "正在編譯",
        "正在整理遊戲資料夾", "顯示詳細資訊", "隱藏詳細資訊", "取消", "返回", "開啟資料夾", "開始遊戲", "你的遊戲已準備就緒", "用時",
        "建置已停止", "建置已取消", "你可以從同一資料夾重新開始。", "要停止建置並關閉嗎?", "語言",
        "你的遊戲", "未安裝", "已是最新 · {0}", "有可用更新 · 目前為 {0}", "已安裝", "也安裝在 {0}", "它的資料夾 {0} 中有 {1}:安裝到那裡會取代 {1}。請移動該遊戲,或為你的遊戲選擇其他資料夾。", "安裝", "更新", "全部更新",
        "選擇你的 {0} 副本 (.nup)", "選擇包含 {0} 的 romfs 和 exefs 的資料夾", "該資料夾中沒有 romfs 和 exefs 資料夾。", "現在更新這些遊戲嗎?存檔會保留。", "所有已安裝的遊戲都是最新的。", "正在更新你的遊戲",
        "正在更新 {0} ({1}/{2})", "你的遊戲已更新", "部分遊戲未能更新", "{0}:已更新到 {1}", "{0}:已是最新 ({1})", "{0}:將從 {1} 更新", "{0}:將會更新", "{0}:未安裝", "{0}:失敗:{1}",
        "在支援更新之前建置:請選擇一次此遊戲的檔案以更新它", "{0}:請選擇一次此遊戲的檔案以更新它",
        "從你自己的副本安裝遊戲,或更新已有的遊戲。",
        "全部更新已停止",
        "停止前已更新的遊戲已完成更新;其他遊戲保持原樣,仍可遊玩。",
        "{0}:未更新:在此遊戲之前已停止",
    };

    public static readonly Language[] All =
    {
        new("en-US", "English (US)", En), new("en-GB", "English (UK)", En), new("fr", "Français", Fr), new("fr-CA", "Français (Canada)", Fr),
        new("de", "Deutsch", De), new("it", "Italiano", It), new("es", "Español (España)", Es), new("es-419", "Español (Latinoamérica)", Es),
        new("pt-BR", "Português (Brasil)", Pt), new("nl", "Nederlands", Nl), new("pl", "Polski", Pl), new("ru", "Русский", Ru),
        new("ja", "日本語", Ja), new("ko", "한국어", Ko), new("zh-Hans", "简体中文", ZhHans), new("zh-Hant", "繁體中文", ZhHant),
    };

    /// The language closest to Windows' own: the exact one, then the same family, else English (US).
    public static Language Default()
    {
        var c = CultureInfo.CurrentUICulture;
        var name = c.Name;
        var pick = All.FirstOrDefault(l => l.Code.Equals(name, StringComparison.OrdinalIgnoreCase));
        if (pick != null) return pick;
        if (name.StartsWith("zh")) return All.First(l => l.Code == (name.Contains("TW") || name.Contains("HK") || name.Contains("Hant") ? "zh-Hant" : "zh-Hans"));
        if (name.StartsWith("es-") && name != "es-ES") return All.First(l => l.Code == "es-419");
        if (name.StartsWith("pt")) return All.First(l => l.Code == "pt-BR");
        return All.FirstOrDefault(l => l.Code == c.TwoLetterISOLanguageName) ?? All[0];
    }
}
