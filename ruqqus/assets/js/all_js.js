// Using mouse

document.body.addEventListener('mousedown', function() {
  document.body.classList.add('using-mouse');
});

document.body.addEventListener('keydown', function(event) {
  if (event.keyCode === 9) {
    document.body.classList.remove('using-mouse');
  }
});

// 2FA toggle modal

$('#2faModal').on('hidden.bs.modal', function () {

  var box = document.getElementById("2faToggle");
  
  box.checked = !box.checked;

});

//email change

// Show confirm password field when user clicks email box

$('#new_email').on('input', function () {

  var id = document.getElementById("email-password");
  var id2 = document.getElementById("email-password-label");
  var id3 = document.getElementById("emailpasswordRequired");

  id.classList.remove("d-none");
  id2.classList.remove("d-none");
  id3.classList.remove("d-none");

});

//GIFS

  // Identify which comment form to insert GIF into

  var commentFormID;

  function commentForm(form) {
    commentFormID = form;
  };

  function getEmoji(searchTerm) {

    var emoji = ' :'+searchTerm+': '

    var commentBox = document.getElementById(commentFormID);

    var old = commentBox.value;

    commentBox.value = old + emoji;

    // Fire the target field's own oninput handler (e.g. the title's
    // overflow sync, or checkForRequired) since setting .value directly
    // doesn't trigger it the way real typing would.
    commentBox.dispatchEvent(new Event('input', { bubbles: true }));

  }

  function loadEmojis() {

    const emojis = [
    {
      type:'ruqqus',
      folder: 'emojis',
      emojis: [
      '500',
      '503',
      'poggers',
      'ruqqking',
      'ruqqie',
      'ruqqie-cowboy',
      'ruqqie-love',
      'ruqqie-pensive',
      'ruqqie-sip',
      'ruqqie-upvote',
      'dexter',
      ]
    },
    {
      type:'animated',
      folder: 'primojis',
      emojis: [
      'anime-dance',
      'cat-vibe',
      'crumb-dance',
      'dance-kid',
      'dance',
      'falling-kermit',
      'minecraft',
      'pepe-saber',
      'pop-cat',
      'wavy-sad',
      'who',
      'yoshi-hammer',
      'lmao-spin',
      'gigachad-gif',
      'yes-cat',
      'no-cat',
      'is4me',
      'frogroll',
      'alert',
      'shrug',
      ]
    },
    {
      type:'reaction',
      folder: 'emojis',
      emojis: [
      'finger-guns',
      'isforme',
      'lmao',
      'lfg',
      'pepe-blanket',
      'pepe-cozy',
      'pepe-sus',
      'pepe-toast',
      'pepe-flushed',
      'weirdhd',
      'copium',
      'clueless',
      'trollface',
      'gigachad',
      'hahayes',
      'mikewazowski',
      'breadstare',
      'pain',
      'tobey-sad',
      'facepalm',
      'kekw',
      'jackie-chan-wtf',
      'leo',
      'pathetic',
      'pogchamp',
      'press-f',
      'shrek-yep',
      'strong-doge',
      'stonks',
      'not-stonks',
      'think-mark',
      'tom-unsettled',
      'vue',
      'this-tbh',
      'king',
      ]
    },
    {
      type: 'classic',
      folder: 'emojis',
      emojis: [
      '2thumbsup',
      'aliendj',
      'ambulance',
      'angry',
      'angrywhip',
      'argue',
      'aroused',
      'ashamed',
      'badass',
      'banana',
      'band',
      'banghead',
      'batman',
      'bigeyes',
      'bite',
      'blind',
      'blowkiss',
      'blush',
      'bong',
      'bounce',
      'bow',
      'breakheart',
      'bs',
      'cartwheel',
      'cat',
      'celebrate',
      'chad',
      'chainsaw',
      'cheers',
      'clap',
      'cold',
      'confused',
      'crazyeyes',
      'cry',
      'cthulhu',
      'cute',
      'D',
      'daydream',
      'ddr',
      'deadpool',
      'devilsmile',
      'diddle',
      'die',
      'distress',
      'disturbing',
      'dizzy',
      'domo',
      'doughboy',
      'drink',
      'drool',
      'dudeweedlmao',
      'edward',
      'electro',
      'elephant',
      'embarrassed',
      'emo',
      'emo2',
      'evil',
      'evilclown',
      'evilgrin',
      'facepalm',
      'fap',
      'flamethrower',
      'flipbird',
      'flirt',
      'frown',
      'gasp',
      'glomp',
      'go',
      'gooby',
      'grr',
      'gtfo',
      'guitar',
      'haha',
      'handshake',
      'happydance',
      'headbang',
      'heart',
      'heartbeat',
      'hearts',
      'highfive',
      'hmm','hmph',
      'holdhands',
      'horny',
      'hug',
      'hugging',
      'hugs',
      'hump',
      'humpbed',
      'hysterical',
      'ily',
      'inlove',
      'jason',
      'jawdrop',
      'jedi',
      'jester',
      'kaboom',
      'kick',
      'kiss',
      'kitty',
      'laughchair',
      'lick',
      'link',
      'lol',
      'lolbeat',
      'loving',
      'makeout',
      'medal',
      'megaman',
      'megamanguitar',
      'meow',
      'metime',
      'mooning',
      'mummy',
      'na',
      'nauseous',
      'nervous',
      'ninja',
      'nod',
      'nono',
      'omg',
      'onfire',
      'ooo',
      'orly',
      'p',
      'paddle',
      'panda',
      'pandabutt',
      'paranoid',
      'party',
      'pat',
      'peek',
      'pikachu',
      'pimp',
      'plzdie',
      'poke',
      'popcorn',
      'pout',
      'probe',
      'puke',
      'punch',
      'quote',
      'raccoon',
      'roar',
      'rofl',
      'roflmao',
      'rolleyes',
      'sad',
      'sadeyes',
      'sadhug',
      'samurai',
      'sarcasm',
      'scoot',
      'scream',
      'shmoopy',
      'shrug',
      'skull',
      'slap',
      'slapfight',
      'sleepy',
      'smackfish',
      'smackhead',
      'smh',
      'smile',
      'smoke',
      'sonic',
      'spank',
      'sparta',
      'sperm',
      'spiderman',
      'stab',
      'star',
      'stare',
      'stfu',
      'suicide',
      'surprisehug',
      'suspicious',
      'sweat',
      'swordfight',
      'taco',
      'talk2hand',
      'tantrum',
      'teehee',
      'thinking',
      'threesome',
      'throw',
      'throwaway',
      'tickle',
      'typing',
      'uhuh',
      'vampbat',
      'viking',
      'violin',
      'vulgar',
      'wah',
      'wat',
      'whip',
      'whipping',
      'wink',
      'witch',
      'wizard',
      'woah',
      'woo','work',
      'worship',
      'wow',
      'XD',
      'yay',
      'zzz'
      ]
    }
    ]

    for (i=0; i < emojis.length; i++) {

      let container = document.getElementById(`EMOJIS_${emojis[i].type}`);
      if (!container) continue; // no matching tab for this category
      let str = '';
      let arr = emojis[i].emojis;
      let dir = emojis[i].folder;

      for (j=0; j < arr.length; j++) {
        str += `<button class="btn btn-white m-1 p-0" onclick="getEmoji(\'${arr[j]}\')" style="width:45px; height:45px; overflow: hidden;"><img width=35 src="/assets/images/${dir}/${arr[j]}" alt="${arr[j]}-emoji"/></button>`;
      }

      container.innerHTML = str
    }

  }

	function getGif(searchTerm) {

		if (searchTerm !== undefined) {
			document.getElementById('gifSearch').value = searchTerm;
		}
		else {
			document.getElementById('gifSearch').value = null;
		}

			// load more gifs div
			var loadGIFs = document.getElementById('gifs-load-more');

			// error message div
			var noGIFs = document.getElementById('no-gifs-found');

			// categories div
			var cats = document.getElementById('GIFcats');

			// container div
			var container = document.getElementById('GIFs');

			// modal body div
			var modalBody = document.getElementById('gif-modal-body')

			// UI buttons
			var backBtn = document.getElementById('gifs-back-btn');
			var cancelBtn = document.getElementById('gifs-cancel-btn');

			container.innerHTML = '';

			if (searchTerm == undefined) {
				container.innerHTML = '<div class="card" onclick="getGif(\'agree\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Agree</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/wGhYz3FHaRJgk/200w_d.gif"> </div> <div class="card" onclick="getGif(\'laugh\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Laugh</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/O5NyCibf93upy/200w_d.gif"> </div> <div class="card" onclick="getGif(\'confused\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Confused</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3o7btPCcdNniyf0ArS/200w_d.gif"> </div> <div class="card" onclick="getGif(\'sad\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Sad</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/ISOckXUybVfQ4/200w_d.gif"> </div> <div class="card" onclick="getGif(\'happy\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Happy</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/XR9Dp54ZC4dji/200w_d.gif"> </div> <div class="card" onclick="getGif(\'awesome\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Awesome</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3ohzdIuqJoo8QdKlnW/200w_d.gif"> </div> <div class="card" onclick="getGif(\'yes\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Yes</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/J336VCs1JC42zGRhjH/200w_d.gif"> </div> <div class="card" onclick="getGif(\'no\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">No</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1zSz5MVw4zKg0/200w_d.gif"> </div> <div class="card" onclick="getGif(\'love\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Love</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/4N1wOi78ZGzSB6H7vK/200w_d.gif"> </div> <div class="card" onclick="getGif(\'please\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Please</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/qUIm5wu6LAAog/200w_d.gif"> </div> <div class="card" onclick="getGif(\'scared\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Scared</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/bEVKYB487Lqxy/200w_d.gif"> </div> <div class="card" onclick="getGif(\'angry\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Angry</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/12Pb87uq0Vwq2c/200w_d.gif"> </div> <div class="card" onclick="getGif(\'awkward\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Awkward</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/unFLKoAV3TkXe/200w_d.gif"> </div> <div class="card" onclick="getGif(\'cringe\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Cringe</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1jDvQyhGd3L2g/200w_d.gif"> </div> <div class="card" onclick="getGif(\'omg\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">OMG</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3o72F8t9TDi2xVnxOE/200w_d.gif"> </div> <div class="card" onclick="getGif(\'why\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Why</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1M9fmo1WAFVK0/200w_d.gif"> </div> <div class="card" onclick="getGif(\'gross\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Gross</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/pVAMI8QYM42n6/200w_d.gif"> </div> <div class="card" onclick="getGif(\'meh\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Meh</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/xT77XTpyEzJ4OJO06c/200w_d.gif"> </div>'
				backBtn.innerHTML = null;
				cancelBtn.innerHTML = null;
				noGIFs.innerHTML = null;
				loadGIFs.innerHTML = null;
			} else {
				backBtn.innerHTML = '<button class="btn btn-link pl-0 pr-3" id="gifs-back-btn" onclick="getGif();"><i class="fas fa-long-arrow-left text-muted"></i></button>';

				cancelBtn.innerHTML = '<button class="btn btn-link pl-3 pr-0" id="gifs-cancel-btn" onclick="getGif();"><i class="fas fa-times text-muted"></i></button>';

				let gifs = [];
				let apiKey = tenor_api_key();
				let lmt = 30;
				let url = "https://g.tenor.com/v1/search?q=" + searchTerm + "&key=" + apiKey + "&limit=" + lmt;
				fetch(url)
				.then(response => {
					return response.json();
				})
				.then(json => {
					let results = Array.isArray(json.results) ? json.results.map(function(obj) {
						return {
							id: obj.id,
							preview: obj.media[0].tinygif.url,
							url: obj.media[0].gif.url,
							source: obj.url,
							bgColor: obj.bg_color
						}
					}) : [];

					gifs = results

					// loop for fetching mutliple GIFs and creating the card divs
					if (gifs.length) {
						for (var i = 0; i < gifs.length; i++) {
							container.innerHTML += ('<div class="card bg-white" style="overflow: hidden" data-dismiss="modal" aria-label="Close" onclick="insertGIF(\'' + gifs[i].url + '\',\'' + commentFormID + '\')"><div class="gif-cat-overlay"></div><img class="img-fluid" src="' + gifs[i].preview + '"></div>');
							noGIFs.innerHTML = null;
							loadGIFs.innerHTML = '<div class="text-center py-3"><div class="mb-3"><i class="fad fa-grin-beam-sweat text-gray-500" style="font-size: 3.5rem;"></i></div><p class="font-weight-bold text-gray-500 mb-0">Thou&#39;ve reached the end of the list!</p></div>';
						}
					} else {
						noGIFs.innerHTML = '<div class="text-center py-3 mt-3"><div class="mb-3"><i class="fad fa-frown text-gray-500" style="font-size: 3.5rem;"></i></div><p class="font-weight-bold text-gray-500 mb-0">Aw shucks. No GIFs found...</p></div>';
						container.innerHTML = null;
						loadGIFs.innerHTML = null;
					}
				})
				.catch(err => {
					noGIFs.innerHTML = '<div class="text-center py-3 mt-3"><div class="mb-3"><i class="fad fa-frown text-gray-500" style="font-size: 3.5rem;"></i></div><p class="font-weight-bold text-gray-500 mb-0">Aw shucks. No GIFs found...</p></div>';
					container.innerHTML = null;
					loadGIFs.innerHTML = null;
				});
			};
		}

  // Insert GIF markdown into comment box function

  function insertGIF(url,form) {

    var gif = "![](" + url +")";

    var commentBox = document.getElementById(form);

    var old  = commentBox.value;

    commentBox.value = old + gif;

    // Fire the target field's own oninput handler (e.g. the title's
    // overflow sync, or checkForRequired) since setting .value directly
    // doesn't trigger it the way real typing would.
    commentBox.dispatchEvent(new Event('input', { bubbles: true }));

  }

  // When GIF keyboard is hidden, hide all GIFs

  $('#gifModal').on('hidden.bs.modal', function (e) {

    document.getElementById('gifSearch').value = null;

    // load more gifs div

    var loadGIFs = document.getElementById('gifs-load-more');

    // no GIFs div

    var noGIFs = document.getElementById('no-gifs-found');

    // container div

    var container = document.getElementById('GIFs');

    // UI buttons

    var backBtn = document.getElementById('gifs-back-btn');

    var cancelBtn = document.getElementById('gifs-cancel-btn');

    // Remove inner HTML from container var

    container.innerHTML = '<div class="card" onclick="getGif(\'agree\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Agree</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/wGhYz3FHaRJgk/200w_d.gif"> </div> <div class="card" onclick="getGif(\'laugh\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Laugh</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/O5NyCibf93upy/200w_d.gif"> </div> <div class="card" onclick="getGif(\'confused\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Confused</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3o7btPCcdNniyf0ArS/200w_d.gif"> </div> <div class="card" onclick="getGif(\'sad\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Sad</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/ISOckXUybVfQ4/200w_d.gif"> </div> <div class="card" onclick="getGif(\'happy\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Happy</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/XR9Dp54ZC4dji/200w_d.gif"> </div> <div class="card" onclick="getGif(\'awesome\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Awesome</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3ohzdIuqJoo8QdKlnW/200w_d.gif"> </div> <div class="card" onclick="getGif(\'yes\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Yes</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/J336VCs1JC42zGRhjH/200w_d.gif"> </div> <div class="card" onclick="getGif(\'no\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">No</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1zSz5MVw4zKg0/200w_d.gif"> </div> <div class="card" onclick="getGif(\'love\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Love</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/4N1wOi78ZGzSB6H7vK/200w_d.gif"> </div> <div class="card" onclick="getGif(\'please\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Please</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/qUIm5wu6LAAog/200w_d.gif"> </div> <div class="card" onclick="getGif(\'scared\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Scared</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/bEVKYB487Lqxy/200w_d.gif"> </div> <div class="card" onclick="getGif(\'angry\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Angry</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/12Pb87uq0Vwq2c/200w_d.gif"> </div> <div class="card" onclick="getGif(\'awkward\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Awkward</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/unFLKoAV3TkXe/200w_d.gif"> </div> <div class="card" onclick="getGif(\'cringe\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Cringe</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1jDvQyhGd3L2g/200w_d.gif"> </div> <div class="card" onclick="getGif(\'omg\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">OMG</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/3o72F8t9TDi2xVnxOE/200w_d.gif"> </div> <div class="card" onclick="getGif(\'why\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Why</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/1M9fmo1WAFVK0/200w_d.gif"> </div> <div class="card" onclick="getGif(\'gross\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Gross</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/pVAMI8QYM42n6/200w_d.gif"> </div> <div class="card" onclick="getGif(\'meh\');" style="overflow: hidden;"> <div class="gif-cat-overlay"> <div style="position: relative;top: 50%;transform: translateY(-50%);color: #ffffff;font-weight: bold;">Meh</div> </div> <img class="img-fluid" src="https://media.giphy.com/media/xT77XTpyEzJ4OJO06c/200w_d.gif"> </div>'

    // Hide UI buttons

    backBtn.innerHTML = null;

    cancelBtn.innerHTML = null;

    // Remove inner HTML from no gifs div

    noGIFs.innerHTML = null;

    // Hide no more gifs div

    loadGIFs.innerHTML = null;

  });

// comment collapse

// Toggle comment collapse

function collapse_comment(comment_id) {

  var comment = "comment-" + comment_id;

  document.getElementById(comment).classList.toggle("collapsed");

};

// Toggle a long comment's collapsed overflow text
function toggleCommentOverflow(comment_id, linkEl) {

  var overflow = document.getElementById("comment-overflow-" + comment_id);
  var expanded = !overflow.classList.contains("d-none");

  overflow.classList.toggle("d-none");
  linkEl.textContent = expanded ? "Show more" : "Show less";

};

// Reveal a blurred sensitive-content wrapper for this page view only -
// pure client-side state, no server call, so it re-blurs on reload.
function revealSensitive(wrapId) {

  var wrap = document.getElementById(wrapId);
  if (wrap) wrap.classList.add("revealed");

};

// Hide a hotlinked media block whose image failed to load (broken link,
// hotlink protection, deleted source) rather than showing a broken-image glyph.
function hideBrokenMedia(imgEl) {
  var wrap = imgEl.closest('.post-media-frame, .embed-lg, .post-preview-lg');
  if (wrap) wrap.style.display = 'none';
};

// Expand the navbar's desktop search form into its own row on mobile,
// where it's normally hidden in favor of a compact search icon.
function toggleMobileSearch() {
  var wrap = document.getElementById('navbar-search-wrap');
  if (!wrap) return;
  wrap.classList.toggle('search-open');
  if (wrap.classList.contains('search-open')) {
    var input = wrap.querySelector('input[type=search]');
    if (input) input.focus();
  }
};

// Text Area Input handling

function textAreaOnKeyDown(e, func){
  if (isCtrlEnterSubmit(e))
    func();

  return;
}

function isCtrlEnterSubmit(e) {
  // If the user has pressed enter + ctrl/command
  if ((e.keyCode == 10 || e.keyCode == 13) && (e.ctrlKey || e.metaKey))
  {
     return true;
  }
  
  return false;
}

// Commenting form

// Expand comment box on focus, hide otherwise

$('.comment-box').focus(function (event) {
  event.preventDefault();

  $(this).parent().parent().addClass("collapsed");

});


/*
$('.comment-box').blur(function () {
    event.preventDefault();

    $(this).parent().parent().removeClass("collapsed");
});

*/

// Comment edit form

toggleEdit=function(id){
  comment=document.getElementById("comment-text-"+id);
  form=document.getElementById("comment-edit-"+id);
  box=document.getElementById('edit-box-comment-'+id);
  actions = document.getElementById('comment-' + id +'-actions');

  comment.classList.toggle("d-none");
  form.classList.toggle("d-none");
  actions.classList.toggle("d-none");
  autoExpand(box);
};

// Post edit form

togglePostEdit=function(id){

  body=document.getElementById("post-body");
  form=document.getElementById("edit-post-body-"+id);
  box=document.getElementById("post-edit-box-"+id);

  body.classList.toggle("d-none");
  form.classList.toggle("d-none");
  autoExpand(box);
};

//comment modding
function removeComment(post_id) {
  url="/api/ban_comment/"+post_id

  callback=function(){
    document.getElementById("comment-"+post_id+"-only").classList.add("banned");

    button=document.getElementById("moderate-"+post_id);
    button.onclick=function(){approveComment(post_id)};
    button.innerHTML="approve"
  }
  post(url, callback, "Unable to remove post at this time. Please try again later.")
};

function approveComment(post_id) {
  url="/api/unban_comment/"+post_id

  callback=function(){
    document.getElementById("comment-"+post_id+"-only").classList.remove("banned");

    button=document.getElementById("moderate-"+post_id);
    button.onclick=function(){removeComment(post_id)};
    button.innerHTML="remove"
  }

  post(url, callback, "Unable to approve post at this time. Please try again later.")
}

admin_comment=function(cid){


  var xhr = new XMLHttpRequest();
  xhr.open("post", "/api/distinguish_comment/"+cid);

  var form = new FormData();

  form.append('formkey', formkey());

  xhr.withCredentials=true;
  xhr.onload=function(){
    if (xhr.status==200) {
      comment=document.getElementById('comment-'+cid+'-only');
      comment.innerHTML=JSON.parse(xhr.response)["html"];
    }
    else {
      var commentError = document.getElementById("comment-error-text");
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-error').toast('show');
      commentError.textContent = JSON.parse(xhr.response)["error"];
    }
  }
  xhr.send(form)
}



//comment forms

// https://stackoverflow.com/a/42183824/11724748

/*
function toggleDropdown(e) {
    const _d = $(e.target).closest('.dropdown'),
        _m = $('.dropdown-menu', _d);
    setTimeout(function () {
        const shouldOpen = e.type !== 'click' && _d.is(':hover');
        _m.toggleClass('show', shouldOpen);
        _d.toggleClass('show', shouldOpen);
        $('[data-toggle="dropdown"]', _d).attr('aria-expanded', shouldOpen);
    }, e.type === 'mouseleave' ? 150 : 0);
}

// Display profile card on hover

$('body')
    .on('mouseenter mouseleave', '.user-profile', toggleDropdown)
    .on('click', '.dropdown-menu a', toggleDropdown);

// Toggle comment collapse

$(".toggle-collapse").click(function (event) {
    event.preventDefault();

    var id = $(this).parent().attr("id");

    document.getElementById(id).classList.toggle("collapsed");
});
*/


//Autoexpand textedit comments

function autoExpand (field) {

  //get current scroll position
  xpos=window.scrollX;
  ypos=window.scrollY;

  // Reset field height
  field.style.height = 'inherit';

  // Get the computed styles for the element
  var computed = window.getComputedStyle(field);

  // Calculate the height
  var height = parseInt(computed.getPropertyValue('border-top-width'), 10)
  + parseInt(computed.getPropertyValue('padding-top'), 10)
  + field.scrollHeight
  + parseInt(computed.getPropertyValue('padding-bottom'), 10)
  + parseInt(computed.getPropertyValue('border-bottom-width'), 10)
  + 32;

  field.style.height = height + 'px';

  //keep window position from changing
  window.scrollTo(xpos,ypos);

};

document.addEventListener('input', function (event) {
  if (event.target.tagName.toLowerCase() !== 'textarea') return;
  // The post composer's title (fixed single-line, horizontal-scroll) and
  // body (fixed initial size, its own scrollbar) are deliberately NOT
  // auto-growing - only comment/edit boxes use this behavior.
  if (event.target.id === 'post-title' || event.target.id === 'post-body') return;
  autoExpand(event.target);
}, false);

//dark mode

function switch_css() {
  css = document.getElementById("css-link");
  dswitch = document.getElementById("dark-switch");
  dswitchmobile = document.getElementById("dark-switch-mobile");

  if (css.href.includes("/assets/style/main.css")) {
    post("/settings/dark_mode/1",
      callback=function(){
        try{ console.log('[switch_css] callback dark enabled branch'); }catch(e){}
        css.href="/assets/style/main_dark.css?v=2.38.0";
        localStorage.setItem('dark_mode_enabled', 'true');
        // Update all dark-switch icons
        document.querySelectorAll('[id="dark-switch"]').forEach(function(d){
          d.classList.remove("fa-toggle-off");
          d.classList.add("fa-toggle-on");
        });
        if(dswitchmobile) {
          dswitchmobile.classList.remove("fa-toggle-off");
          dswitchmobile.classList.add("fa-toggle-on");
        }
	$('body').toggleClass('light');
	$('body').toggleClass('dark');
        // set labels based on the new icon state
        // update global toggle anchors for switch_css directly (avoid helper)
        document.querySelectorAll('[onclick]').forEach(function(a){
          var onclick = a.getAttribute && a.getAttribute('onclick');
          if(!onclick || onclick.indexOf('switch_css') === -1) return;
          var icon = a.querySelector('i');
          if(!icon) return;
          try { a.innerHTML = icon.outerHTML + 'Light mode'; } catch(e){}
        });
      }
      );
  }
  else {
    post("/settings/dark_mode/0",
      callback=function(){
        try{ console.log('[switch_css] callback dark disabled branch'); }catch(e){}
        css.href="/assets/style/main.css?v=2.38.0";
        localStorage.setItem('dark_mode_enabled', 'false');
        // Update all dark-switch icons
        document.querySelectorAll('[id="dark-switch"]').forEach(function(d){
          d.classList.remove("fa-toggle-on");
          d.classList.add("fa-toggle-off");
        });
        if(dswitchmobile) {
          dswitchmobile.classList.remove("fa-toggle-on");
          dswitchmobile.classList.add("fa-toggle-off");
        }
	$('body').toggleClass('light');
	$('body').toggleClass('dark');
        document.querySelectorAll('[onclick]').forEach(function(a){
          var onclick = a.getAttribute && a.getAttribute('onclick');
          if(!onclick || onclick.indexOf('switch_css') === -1) return;
          var icon = a.querySelector('i');
          if(!icon) return;
          try { a.innerHTML = icon.outerHTML + 'Dark mode'; } catch(e){}
        });
      }
      );
  }
}

// Initialize dark mode toggle text on page load
document.addEventListener('DOMContentLoaded', function() {
  var css = document.getElementById("css-link");
  if(!css) return;
  
  var isDarkMode = css.href.includes("/assets/style/main_dark.css");
  var localStorageDarkMode = localStorage.getItem('dark_mode_enabled');
  
  // If localStorage differs from server state, update the CSS and sync
  if(localStorageDarkMode !== null && ((localStorageDarkMode === 'true') !== isDarkMode)) {
    isDarkMode = localStorageDarkMode === 'true';
    if(isDarkMode) {
      css.href="/assets/style/main_dark.css?v=2.38.0";
      $('body').addClass('dark').removeClass('light');
    } else {
      css.href="/assets/style/main.css?v=2.38.0";
      $('body').addClass('light').removeClass('dark');
    }
  }
  
  // Update toggle icons and text
  document.querySelectorAll('[id="dark-switch"]').forEach(function(d){
    if(isDarkMode) {
      d.classList.remove("fa-toggle-off");
      d.classList.add("fa-toggle-on");
    } else {
      d.classList.remove("fa-toggle-on");
      d.classList.add("fa-toggle-off");
    }
  });
  
  document.querySelectorAll('[onclick]').forEach(function(a){
    var onclick = a.getAttribute && a.getAttribute('onclick');
    if(!onclick || onclick.indexOf('switch_css') === -1) return;
    var icon = a.querySelector('i');
    if(!icon) return;
    try { 
      a.innerHTML = icon.outerHTML + (isDarkMode ? 'Light mode' : 'Dark mode'); 
    } catch(e){}
  });
});

// Delete Post

function delete_postModal(id) {

  // Passed data for modal

  document.getElementById("deletePostButton-mobile").addEventListener("click", delete_post);

  document.getElementById("deletePostButton").addEventListener("click", delete_post);

  function delete_post(){  

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Deleting post';  
    this.disabled = true; 
    post('/delete_post/' + id,
      callback = function() {

        location.reload();
      }
      )
  }

};

// Delete Comment

function delete_commentModal(id) {

  // Passed data for modal

  document.getElementById("deleteCommentButton").onclick = function() {  

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Deleting comment';  
    this.disabled = true; 
    post('/delete/comment/' + id,
      callback = function() {

        location.reload();
      }
      )
  }

};

//Email verification text

function emailVerifyText() {

  document.getElementById("email-verify-text").innerHTML = "Verification email sent! Please check your inbox.";

}

//flagging
// Flag Comment

report_commentModal = function(id, author) {

  document.getElementById("comment-author").textContent = author;

  //offtopic.disabled=true;

  document.getElementById("reportCommentButton").onclick = function() {

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Reporting comment';
    this.disabled = true;
    post('/api/flag/comment/' + id,
      callback = function() {

        document.getElementById("reportCommentFormBefore").classList.add('d-none');
        document.getElementById("reportCommentFormAfter").classList.remove('d-none');
      }
      )
  }

};

$('#reportCommentModal').on('hidden.bs.modal', function () {

  var button = document.getElementById("reportCommentButton");

  var beforeModal = document.getElementById("reportCommentFormBefore");
  var afterModal = document.getElementById("reportCommentFormAfter");

  button.innerHTML='Report comment';
  button.disabled= false;
  afterModal.classList.add('d-none');

  if ( beforeModal.classList.contains('d-none') ) {
    beforeModal.classList.remove('d-none');
  }

});


// Flag Submission

report_postModal = function(id, author, board) {

  document.getElementById("post-author").textContent = author;

  offtopic=document.getElementById('report-post-to-guild-dropdown-option');
  offtopic.innerHTML= 'This post is off-topic for +' + board;

  if (board=='general') {
    offtopic.disabled=true;
  }
  else {
    offtopic.disabled=false;
  }

  selectbox=document.getElementById('report-type-dropdown');
  selectbox.value='reason_not_selected';

  submitbutton=document.getElementById("reportPostButton");
  submitbutton.disabled=true;

  submitbutton.onclick = function() {

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Reporting post';
    this.disabled = true;

    var xhr = new XMLHttpRequest();
    xhr.open("POST", '/api/flag/post/'+id, true);
    var form = new FormData()
    form.append("formkey", formkey());

    dropdown=document.getElementById("report-type-dropdown");
    form.append("report_type", dropdown.options[dropdown.selectedIndex].value);

    xhr.withCredentials=true;

    xhr.onload=function() {
      document.getElementById("reportPostFormBefore").classList.add('d-none');
      document.getElementById("reportPostFormAfter").classList.remove('d-none');
    };

    xhr.onerror=function(){alert(errortext)};
    xhr.send(form);

  }
};

$('#reportPostModal').on('hidden.bs.modal', function () {

  var button = document.getElementById("reportPostButton");

  var beforeModal = document.getElementById("reportPostFormBefore");
  var afterModal = document.getElementById("reportPostFormAfter");

  button.innerHTML='Report post';
  button.disabled= false;

  afterModal.classList.add('d-none');

  if ( beforeModal.classList.contains('d-none') ) {
    beforeModal.classList.remove('d-none');
  }

});

// Forward Post

forward_postModal = function(id) {

  var input = document.getElementById('forward-guild-name');
  input.value = '';

  var errorBox = document.getElementById('forwardPostError');
  errorBox.classList.add('d-none');
  errorBox.textContent = '';

  var submitbutton = document.getElementById("forwardPostButton");
  submitbutton.disabled = false;
  submitbutton.innerHTML = 'Forward';

  submitbutton.onclick = function() {

    var name = input.value.trim().replace(/^\+/, '');
    if (!name) { return; }

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Forwarding';
    this.disabled = true;

    var xhr = new XMLHttpRequest();
    xhr.open("POST", '/post/'+id+'/forward', true);
    var form = new FormData();
    form.append("formkey", formkey());
    form.append("board", name);
    xhr.withCredentials = true;

    var thisButton = this;

    xhr.onload = function() {
      if (xhr.status >= 200 && xhr.status < 300) {
        document.getElementById("forwardPostFormBefore").classList.add('d-none');
        document.getElementById("forwardPostFormAfter").classList.remove('d-none');
        window.location.reload(true);
      } else {
        var data;
        try { data = JSON.parse(xhr.response); } catch(e) { data = {}; }
        errorBox.textContent = data.error || 'Unable to forward this post.';
        errorBox.classList.remove('d-none');
        thisButton.disabled = false;
        thisButton.innerHTML = 'Forward';
      }
    };
    xhr.onerror = function() {
      errorBox.textContent = 'Unable to forward this post.';
      errorBox.classList.remove('d-none');
      thisButton.disabled = false;
      thisButton.innerHTML = 'Forward';
    };
    xhr.send(form);
  };
};

$('#forwardPostModal').on('hidden.bs.modal', function () {

  var button = document.getElementById("forwardPostButton");

  var beforeModal = document.getElementById("forwardPostFormBefore");
  var afterModal = document.getElementById("forwardPostFormAfter");

  button.innerHTML='Forward';
  button.disabled= false;

  afterModal.classList.add('d-none');

  if ( beforeModal.classList.contains('d-none') ) {
    beforeModal.classList.remove('d-none');
  }

  document.getElementById('forwardPostError').classList.add('d-none');

});

forward_commentModal = function(id) {

  var guildInput = document.getElementById('promote-guild-name');
  guildInput.value = '';

  var errorBox = document.getElementById('forwardCommentError');
  errorBox.classList.add('d-none');
  errorBox.textContent = '';

  var submitbutton = document.getElementById("forwardCommentButton");
  submitbutton.disabled = false;
  submitbutton.innerHTML = 'Forward';

  submitbutton.onclick = function() {

    var name = guildInput.value.trim().replace(/^\+/, '');
    if (!name) { return; }

    this.innerHTML='<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Forwarding';
    this.disabled = true;

    var xhr = new XMLHttpRequest();
    xhr.open("POST", '/comment/'+id+'/forward', true);
    var form = new FormData();
    form.append("formkey", formkey());
    form.append("board", name);
    xhr.withCredentials = true;

    var thisButton = this;

    xhr.onload = function() {
      if (xhr.status >= 200 && xhr.status < 300) {
        document.getElementById("forwardCommentFormBefore").classList.add('d-none');
        document.getElementById("forwardCommentFormAfter").classList.remove('d-none');
        window.location.reload(true);
      } else {
        var data;
        try { data = JSON.parse(xhr.response); } catch(e) { data = {}; }
        errorBox.textContent = data.error || 'Unable to forward this comment.';
        errorBox.classList.remove('d-none');
        thisButton.disabled = false;
        thisButton.innerHTML = 'Forward';
      }
    };
    xhr.onerror = function() {
      errorBox.textContent = 'Unable to forward this comment.';
      errorBox.classList.remove('d-none');
      thisButton.disabled = false;
      thisButton.innerHTML = 'Forward';
    };
    xhr.send(form);
  };
};

$('#forwardCommentModal').on('hidden.bs.modal', function () {

  var button = document.getElementById("forwardCommentButton");

  var beforeModal = document.getElementById("forwardCommentFormBefore");
  var afterModal = document.getElementById("forwardCommentFormAfter");

  button.innerHTML='Forward';
  button.disabled = false;

  afterModal.classList.add('d-none');

  if ( beforeModal.classList.contains('d-none') ) {
    beforeModal.classList.remove('d-none');
  }

  document.getElementById('forwardCommentError').classList.add('d-none');

});

//enlarge thumbs
// Enlarge submissionlisting thumbnail

enlarge_thumb = function(post_id) {

  document.getElementById(post_id).classList.toggle("enlarged");

};

//iOS webapp stuff

(function(document,navigator,standalone) {
            // prevents links from apps from oppening in mobile safari
            // this javascript must be the first script in your <head>
            if ((standalone in navigator) && navigator[standalone]) {
              var curnode, location=document.location, stop=/^(a|html)$/i;
              document.addEventListener('click', function(e) {
                curnode=e.target;
                while (!(stop).test(curnode.nodeName)) {
                  curnode=curnode.parentNode;
                }
                    // Condidions to do this only on links to your own app
                    // if you want all links, use if('href' in curnode) instead.
                    if('href' in curnode && ( curnode.href.indexOf('http') || ~curnode.href.indexOf(location.host) ) ) {
                      e.preventDefault();
                      location.href = curnode.href;
                    }
                  },false);
            }
          })(document,window.navigator,'standalone');


//KC easter egg

$(function(){
  var kKeys = [];
  function Kpress(e){
    kKeys.push(e.keyCode);
    if (kKeys.toString().indexOf("38,38,40,40,37,39,37,39,66,65") >= 0) {
      $(this).unbind('keydown', Kpress);
      kExec();
    }
  }
  $(document).keydown(Kpress);
});
function kExec(){
 $('body').append ('<iframe width="0" height="0" src="https://www.youtube.com/embed/xoEEOrTctpA?rel=0&amp;controls=0&amp;showinfo=0&autoplay=1" frameborder="0" allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>');
 $('a').addClass('ruckus');
 $('p').addClass('ruckus');
 $('img').addClass('ruckus');
 $('span').addClass('ruckus');
 $('button').addClass('ruckus');
 $('i').addClass('ruckus');
 $('input').addClass('ruckus');
};

$('.kick-button-listing').click(function(event) {
  if (event.which != 1) {return}

  boardname=$(this).data('boardname')
  pid=$(this).data('pid')
  action=$(this).data('hidden')=='true' ? 'unhide' : 'kick'

  var replaceCallback = function(xhr){
    $("#post-"+pid).replaceWith(JSON.parse(xhr.response)['data'])
  }

  if (action == 'kick') {
    post_with_reason('/mod/kick/'+ boardname+'/'+pid, replaceCallback)
  } else {
    post_response('/mod/'+action+'/'+ boardname+'/'+pid, replaceCallback)
  }
  }
)

//POST

function post(url, callback, errortext) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  var form = new FormData()
  form.append("formkey", formkey());
  xhr.withCredentials=true;
  xhr.onerror=function() { alert(errortext); };
  xhr.onload = function() {
    if (xhr.status >= 200 && xhr.status < 300) {
      callback();
    } else {
      xhr.onerror();
    }
  };
  xhr.send(form);
};

function post_with_reason(url, callback, errortext) {
  var reason = window.prompt("Why does this break your guild's rules? This will be shown to anyone who looks up this post/comment's history.");
  if (reason == null || reason.trim() == "") {
    return;
  }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  var form = new FormData()
  form.append("formkey", formkey());
  form.append("reason", reason.trim());
  xhr.withCredentials=true;
  xhr.onerror=function() { alert(errortext); };
  xhr.onload = function() {
    if (xhr.status >= 200 && xhr.status < 300) {
      callback(xhr);
    } else {
      xhr.onerror();
    }
  };
  xhr.send(form);
};

function post_response(url, callback, errortext) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  var form = new FormData()
  form.append("formkey", formkey());
  xhr.withCredentials=true;
  xhr.onerror=function() { alert(errortext); };
  xhr.onload = function() {
    if (xhr.status >= 200 && xhr.status < 300) {
      callback(xhr);
    } else {
      xhr.onerror();
    }
  };
  xhr.send(form);
};

// sub/unsub

function toggleSub(){
  document.getElementById('button-unsub').classList.toggle('d-none');
  document.getElementById('button-sub').classList.toggle('d-none');
  document.getElementById('button-unsub-modal').classList.toggle('d-none');
  document.getElementById('button-sub-modal').classList.toggle('d-none');
  document.getElementById('button-unsub-mobile').classList.toggle('d-none');
  document.getElementById('button-sub-mobile').classList.toggle('d-none');
}

function post_toast_with_reason(url, callback) {
  var reason = window.prompt("Why does this break your guild's rules? This will be shown to anyone who looks up this comment's history.");
  if (reason == null || reason.trim() == "") {
    return;
  }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  var form = new FormData()
  form.append("formkey", formkey());
  form.append("reason", reason.trim());
  xhr.withCredentials=true;

  xhr.onload = function() {
    if (xhr.status==204) {}
      else if (xhr.status >= 200 && xhr.status < 300) {
        $('#toast-post-success').toast('dispose');
        $('#toast-post-success').toast('show');
        document.getElementById('toast-post-success-text').innerText = JSON.parse(xhr.response)["message"];
        callback(xhr)
        return true

      } else if (xhr.status >= 300 && xhr.status < 400) {
        window.location.href = JSON.parse(xhr.response)["redirect"]
      } else {
        data=JSON.parse(xhr.response);

        $('#toast-post-error').toast('dispose');
        $('#toast-post-error').toast('show');
        document.getElementById('toast-post-error-text').innerText = data["error"];
        return false

      }
    };

    xhr.send(form);

  }

function post_toast(url, callback) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", url, true);
  var form = new FormData()
  form.append("formkey", formkey());
  xhr.withCredentials=true;

  xhr.onload = function() {
    if (xhr.status==204) {}
      else if (xhr.status >= 200 && xhr.status < 300) {
        $('#toast-post-success').toast('dispose');
        $('#toast-post-success').toast('show');
        document.getElementById('toast-post-success-text').innerText = JSON.parse(xhr.response)["message"];
        callback(xhr)
        return true

      } else if (xhr.status >= 300 && xhr.status < 400) {
        window.location.href = JSON.parse(xhr.response)["redirect"]
      } else {
        data=JSON.parse(xhr.response);

        $('#toast-post-error').toast('dispose');
        $('#toast-post-error').toast('show');
        document.getElementById('toast-post-error-text').innerText = data["error"];
        return false

      }
    };

    xhr.send(form);

  }

// Bell Notifications

$('.bell-button').click(function (event) {

  if (event.which != 1) {
    return
  }

  $('.bell-icon').toggleClass('fa-bell')
  $('.bell-icon').toggleClass('fa-bell-on')
  $('.bell-icon').toggleClass('text-purple')

  post_toast($(this).data('url'))

});


//Admin post modding

function removePost(post_id) {
  url="/api/ban_post/"+post_id

  callback=function(){
    document.getElementById("post-"+post_id).classList.add("banned");

    var button=document.getElementById("moderate-post-"+post_id);
    button.onclick=function(){approvePost(post_id)};
    button.classList.remove("removeDropdownItem");
    button.classList.add("approveDropdownItem");
    button.innerHTML='<i class="fas fa-clipboard-check"></i>Approve'
  }
  post(url, callback, "Unable to remove post at this time. Please try again later.")
}

function approvePost(post_id) {
  url="/api/unban_post/"+post_id

  callback=function(){
    document.getElementById("post-"+post_id).classList.remove("banned");

    var button=document.getElementById("moderate-post-"+post_id);
    button.onclick=function(){removePost(post_id)};
    button.classList.remove("approveDropdownItem");
    button.classList.add("removeDropdownItem");
    button.innerHTML='<i class="fas fa-trash-alt"></i>Remove'
  }

  post(url, callback, "Unable to approve post at this time. Please try again later.")
}

//Element deleter

function deleteElement(eid) {
  x=document.getElementById(eid)
  x.parentElement.removeChild(x)

}


//Signup js
// Display username and password requirements on input

$('#password-register').on('input', function () {

  var charCount = document.getElementById("password-register").value;
  var id = document.getElementById("passwordHelpRegister");
  var successID = document.getElementById("passwordHelpSuccess");

  if (charCount.length >= 8) {
    id.classList.add("d-none");
    successID.classList.remove("d-none");
  }
  else {
    id.classList.remove("d-none");
    successID.classList.add("d-none");
  };

});

// Check username length, special chars

$('#username-register').on('input', function () {

  var charCount = document.getElementById("username-register").value;
  var id = document.getElementById("usernameHelpRegister");
  var successID = document.getElementById("usernameHelpSuccess");

  var ruqqusAPI = '/api/is_available/' + charCount;

  if (charCount.length >= 3) {

    $.getJSON(ruqqusAPI, function(result) {
      $.each(result, function(i, field) {
        if (field == false) {
          id.innerHTML = '<span class="form-text font-weight-bold text-danger mt-1">Username already taken :(';
        }
      });
    });

  }

  if (!/[^a-zA-Z0-9_$]/.test(charCount)) {
    // Change alert text
    id.innerHTML = '<span class="form-text font-weight-bold text-success mt-1">Username is a-okay!';

    if (charCount.length < 3) {
      id.innerHTML = '<span class="form-text font-weight-bold text-muted mt-1">Username must be at least 3 characters long.';
    }
    else if (charCount.length > 25) {
      id.innerHTML = '<span class="form-text font-weight-bold text-danger mt-1">Username must be 25 characters or less.';
    }
  }
  else {
    id.innerHTML = '<span class="form-text font-weight-bold text-danger mt-1">No special characters or spaces allowed.</span>';
  };

});

// Search Icon
// Change navbar search icon when form is in focus, active states

$(".form-control").focus(function () {
  $(this).prev('.input-group-append').removeClass().addClass('input-group-append-focus');
  $(this).next('.input-group-append').removeClass().addClass('input-group-append-focus');
});

$(".form-control").focusout(function () {
  $(this).prev('.input-group-append-focus').removeClass().addClass('input-group-append');
  $(this).next('.input-group-append-focus').removeClass().addClass('input-group-append');
});

//spinner effect

$(document).ready(function() {
  $('#login').submit(function() {
      // disable button
      $("#login_button").prop("disabled", true);
      // add spinner to button
      $("#login_button").html('<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Signing in');
    });
});

$(document).ready(function() {
  $('#signup').submit(function() {
      // disable button
      $("#register_button").prop("disabled", true);
      // add spinner to button
      $("#register_button").html('<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Registering');
    });
});

$(document).ready(function() {
  $('#submitform').submit(function() {
      // disable button
      $("#create_button").prop("disabled", true);
      // add spinner to button
      $("#create_button").html('<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Creating post');
    });
});

// Sidebar collapsing

// Desktop

if (document.getElementById("sidebar-left") && localStorage.sidebar_pref == 'collapsed') {

  document.getElementById('sidebar-left').classList.add('sidebar-collapsed');

};

function toggle_sidebar_collapse() {

  // Store Pref
  localStorage.setItem('sidebar_pref', 'collapsed');

  document.getElementById('sidebar-left').classList.toggle('sidebar-collapsed');

};

function toggle_sidebar_expand() {

  // Remove Pref
  localStorage.removeItem('sidebar_pref');

  document.getElementById('sidebar-left').classList.toggle('sidebar-collapsed');

}

// Voting

var upvote = function(event) {

  var type = event.target.dataset.contentType;
  var id = event.target.dataset.idUp;

  var downvoteButton = document.getElementsByClassName(type + '-' + id + '-down');
  var upvoteButton = document.getElementsByClassName(type + '-' + id + '-up');
  var scoreText = document.getElementsByClassName(type + '-score-' + id);

  for (var j = 0; j < upvoteButton.length && j < downvoteButton.length && j < scoreText.length; j++) {

    var thisUpvoteButton = upvoteButton[j];
    var thisDownvoteButton = downvoteButton[j];
    var thisScoreText = scoreText[j];
    var thisScore = Number(thisScoreText.textContent);

    if (thisUpvoteButton.classList.contains('active')) {
      thisUpvoteButton.classList.remove('active')
      thisScoreText.textContent = thisScore - 1
      voteDirection = "0"
    } else if (thisDownvoteButton.classList.contains('active')) {
      thisUpvoteButton.classList.add('active')
      thisDownvoteButton.classList.remove('active')
      thisScoreText.textContent = thisScore + 2
      voteDirection = "1"
    } else {
      thisUpvoteButton.classList.add('active')
      thisScoreText.textContent = thisScore + 1
      voteDirection = "1"
    }

    if (thisUpvoteButton.classList.contains('active')) {
      thisScoreText.classList.add('score-up')
      thisScoreText.classList.remove('score-down')
      thisScoreText.classList.remove('score')
    } else if (thisDownvoteButton.classList.contains('active')) {
      thisScoreText.classList.add('score-down')
      thisScoreText.classList.remove('score-up')
      thisScoreText.classList.remove('score')
    } else {
      thisScoreText.classList.add('score')
      thisScoreText.classList.remove('score-up')
      thisScoreText.classList.remove('score-down')
    }
  }

  post_toast("/api/vote/" + type + "/" + id + "/" + voteDirection);
  
}

var downvote = function(event) {

  var type = event.target.dataset.contentType;
  var id = event.target.dataset.idDown;

  var downvoteButton = document.getElementsByClassName(type + '-' + id + '-down');
  var upvoteButton = document.getElementsByClassName(type + '-' + id + '-up');
  var scoreText = document.getElementsByClassName(type + '-score-' + id);

  for (var j = 0; j < upvoteButton.length && j < downvoteButton.length && j < scoreText.length; j++) {

    var thisUpvoteButton = upvoteButton[j];
    var thisDownvoteButton = downvoteButton[j];
    var thisScoreText = scoreText[j];
    var thisScore = Number(thisScoreText.textContent);

    if (thisDownvoteButton.classList.contains('active')) {
      thisDownvoteButton.classList.remove('active')
      thisScoreText.textContent = thisScore + 1
      voteDirection = "0"
    } else if (thisUpvoteButton.classList.contains('active')) {
      thisDownvoteButton.classList.add('active')
      thisUpvoteButton.classList.remove('active')
      thisScoreText.textContent = thisScore - 2
      voteDirection = "-1"
    } else {
      thisDownvoteButton.classList.add('active')
      thisScoreText.textContent = thisScore - 1
      voteDirection = "-1"
    }

    if (thisUpvoteButton.classList.contains('active')) {
      thisScoreText.classList.add('score-up')
      thisScoreText.classList.remove('score-down')
      thisScoreText.classList.remove('score')
    } else if (thisDownvoteButton.classList.contains('active')) {
      thisScoreText.classList.add('score-down')
      thisScoreText.classList.remove('score-up')
      thisScoreText.classList.remove('score')
    } else {
      thisScoreText.classList.add('score')
      thisScoreText.classList.remove('score-up')
      thisScoreText.classList.remove('score-down')
    }
  }

  post_toast("/api/vote/" + type + "/" + id + "/" + voteDirection);
  
}

var register_votes = function() {
  var upvoteButtons = document.getElementsByClassName('upvote-button')

  var downvoteButtons = document.getElementsByClassName('downvote-button')

  var voteDirection = 0

  for (var i = 0; i < upvoteButtons.length; i++) {
    upvoteButtons[i].addEventListener('click', upvote, false);
    upvoteButtons[i].addEventListener('keydown', function(event) {
      if (event.keyCode === 13) {
        upvote(event)
      }
    }, false)
  };

  for (var i = 0; i < downvoteButtons.length; i++) {
    downvoteButtons[i].addEventListener('click', downvote, false);
    downvoteButtons[i].addEventListener('keydown', function(event) {
      if (event.keyCode === 13) {
        downvote(event)
      }
    }, false)
  };
}

register_votes()

/*

function vote(post_id, direction) {
  url="/api/vote/post/"+post_id+"/"+direction;

  callback=function(){
    thing = document.getElementById("post-"+post_id);
    uparrow1=document.getElementById("post-"+post_id+"-up");
    downarrow1=document.getElementById("post-"+post_id+"-down");
    scoreup1=document.getElementById("post-"+post_id+"-score-up");
    scorenone1=document.getElementById("post-"+post_id+"-score-none");
    scoredown1=document.getElementById("post-"+post_id+"-score-down");

    thing2=document.getElementById("voting-"+post_id+"-mobile")
    uparrow2=document.getElementById("arrow-"+post_id+"-mobile-up");
    downarrow2=document.getElementById("arrow-"+post_id+"-mobile-down");
    scoreup2=document.getElementById("post-"+post_id+"-score-mobile-up");
    scorenone2=document.getElementById("post-"+post_id+"-score-mobile-none");
    scoredown2=document.getElementById("post-"+post_id+"-score-mobile-down");

    if (direction=="1") {
      thing.classList.add("upvoted");
      thing.classList.remove("downvoted");
      uparrow1.onclick=function(){vote(post_id, 0)};
      downarrow1.onclick=function(){vote(post_id, -1)};
      scoreup1.classList.remove("d-none");
      scorenone1.classList.add("d-none");
      scoredown1.classList.add("d-none");

      thing2.classList.add("upvoted");
      thing2.classList.remove("downvoted");
      uparrow2.onclick=function(){vote(post_id, 0)};
      downarrow2.onclick=function(){vote(post_id, -1)};
      scoreup2.classList.remove("d-none");
      scorenone2.classList.add("d-none");
      scoredown2.classList.add("d-none");
    }
    else if (direction=="-1"){
      thing.classList.remove("upvoted");
      thing.classList.add("downvoted");
      uparrow1.onclick=function(){vote(post_id, 1)};
      downarrow1.onclick=function(){vote(post_id, 0)};
      scoreup1.classList.add("d-none");
      scorenone1.classList.add("d-none");
      scoredown1.classList.remove("d-none");

      thing2.classList.remove("upvoted");
      thing2.classList.add("downvoted");
      uparrow2.onclick=function(){vote(post_id, 1)};
      downarrow2.onclick=function(){vote(post_id, 0)};
      scoreup2.classList.add("d-none");
      scorenone2.classList.add("d-none");
      scoredown2.classList.remove("d-none");

    }
    else if (direction=="0"){
      thing.classList.remove("upvoted");
      thing.classList.remove("downvoted");
      uparrow1.onclick=function(){vote(post_id, 1)};
      downarrow1.onclick=function(){vote(post_id, -1)};
      scoreup1.classList.add("d-none");
      scorenone1.classList.remove("d-none");
      scoredown1.classList.add("d-none");

      thing2.classList.remove("upvoted");
      thing2.classList.remove("downvoted");
      uparrow2.onclick=function(){vote(post_id, 1)};
      downarrow2.onclick=function(){vote(post_id, -1)};
      scoreup2.classList.add("d-none");
      scorenone2.classList.remove("d-none");
      scoredown2.classList.add("d-none");

    }
  }

// helper: update anchor text for toggle links that call switch_css()
function _set_switch_css_text(text) {
  try { console.log('[_set_switch_css_text] called with', text); } catch(e) {}
  // look for any element with an onclick attribute that references switch_css
  document.querySelectorAll('[onclick]').forEach(function(a){
    var onclick = a.getAttribute && a.getAttribute('onclick');
    if(!onclick || onclick.indexOf('switch_css') === -1) return;
    var icon = a.querySelector('i');
    if(!icon) return;
    // replace anchor content with icon + single text node
    try { a.innerHTML = icon.outerHTML + ' ' + text; } catch (e) {}
  });
}

// expose helper to the global window object for debugging and direct calls
try { window._set_switch_css_text = _set_switch_css_text; } catch(e) {}

  post(url, callback, "Unable to vote at this time. Please try again later.");
};

*/

function vote_comment(comment_id, direction) {
  url="/api/vote/comment/"+ comment_id +"/"+direction;

  callback=function(){
    thing = document.getElementById("comment-"+ comment_id+"-actions");
    uparrow1=document.getElementById("comment-"+ comment_id +"-up");
    downarrow1=document.getElementById("comment-"+ comment_id +"-down");
    scoreup1=document.getElementById("comment-"+ comment_id +"-score-up");
    scorenone1=document.getElementById("comment-"+ comment_id +"-score-none");
    scoredown1=document.getElementById("comment-"+ comment_id +"-score-down");

    if (direction=="1") {
      thing.classList.add("upvoted");
      thing.classList.remove("downvoted");
      uparrow1.onclick=function(){vote_comment(comment_id, 0)};
      downarrow1.onclick=function(){vote_comment(comment_id, -1)};
      scoreup1.classList.remove("d-none");
      scorenone1.classList.add("d-none");
      scoredown1.classList.add("d-none");
    }
    else if (direction=="-1"){
      thing.classList.remove("upvoted");
      thing.classList.add("downvoted");
      uparrow1.onclick=function(){vote_comment(comment_id, 1)};
      downarrow1.onclick=function(){vote_comment(comment_id, 0)};
      scoreup1.classList.add("d-none");
      scorenone1.classList.add("d-none");
      scoredown1.classList.remove("d-none");
    }
    else if (direction=="0"){
      thing.classList.remove("upvoted");
      thing.classList.remove("downvoted");
      uparrow1.onclick=function(){vote_comment(comment_id, 1)};
      downarrow1.onclick=function(){vote_comment(comment_id, -1)};
      scoreup1.classList.add("d-none");
      scorenone1.classList.remove("d-none");
      scoredown1.classList.add("d-none");
    }
  }

  post(url, callback, "Unable to vote at this time. Please try again later.");
}

//yt embed

function getId(url) {
  var regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
  var match = url.match(regExp);

  if (match && match[2].length == 11) {
    return match[2];
  } else {
    return 'error';
  }
}

var myUrl = $('#embedURL').text();

myId = getId(myUrl);

$('#ytEmbed').html('<iframe width="100%" height="475" src="//www.youtube.com/embed/' + myId + '" frameborder="0" allowfullscreen></iframe>');


// Expand Images on Desktop

function expandDesktopImage(image) {

// GIPHY attribution div

var attribution = document.getElementById("modal-image-attribution");

// Link text

var linkText = document.getElementById("desktop-expanded-image-link");
var imgLink = document.getElementById("desktop-expanded-image-wrap-link");

var inlineImage = document.getElementById("desktop-expanded-image");

inlineImage.src = image;

linkText.href = image;
imgLink.href = image;

if (image.includes("i.ruqqus.com")) {
  linkText.textContent = 'Go to website';
}
else {
  linkText.textContent = 'View original';
}

};

// When image modal is closed

$('#expandImageModal').on('hidden.bs.modal', function (e) {

    // // attribution div

    // var attribution = document.getElementById("modal-image-attribution");

    // // remove the attribution

    // attribution.innerHTML = null;

  // remove image src and link

  document.getElementById("desktop-expanded-image").src = '';

  document.getElementById("desktop-expanded-image-link").href = '';

});

// Text Formatting

// Bold Text

makeBold = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  var format = '**'

  if (selectedText.includes('**')) {
    text.value = selectedText.replace(/\*/g, '');
    
  }
  else if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + format + selectedText + format + text.value.substring(endIndex);
  }
}

// Italicize Comment Text

makeItalics = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  var format = '*'

  if (selectedText.includes('*')) {
    text.value = selectedText.replace(/\*/g, '');
    
  }
  else if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + format + selectedText + format + text.value.substring(endIndex);
  }
}

// Quote Comment Text

makeQuote = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  var format = '>'

  if (selectedText.includes('>')) {
    text.value = text.value.substring(0, startIndex) + selectedText.replace(/\>/g, '') + text.value.substring(endIndex);
    
  }
  else if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + format + selectedText + text.value.substring(endIndex);
  }
}

// Strikethrough Comment Text

makeStrikethrough = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  var format = '~~'

  if (selectedText.includes('~~')) {
    text.value = text.value.substring(0, startIndex) + selectedText.replace(/~~/g, '') + text.value.substring(endIndex);
  }
  else if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + format + selectedText + format + text.value.substring(endIndex);
  }
}

// Spoiler-tag Comment Text (Reddit-style >!text!< delimiter)

makeSpoiler = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else if (selectedText.startsWith('>!') && selectedText.endsWith('!<')) {
    text.value = text.value.substring(0, startIndex) + selectedText.slice(2, -2) + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + '>!' + selectedText + '!<' + text.value.substring(endIndex);
  }
}

// Inline or fenced Code for Comment Text

makeCode = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
  }
  else if (selectedText.includes('\n')) {
    if (selectedText.startsWith('```\n') && selectedText.endsWith('\n```')) {
      text.value = text.value.substring(0, startIndex) + selectedText.slice(4, -4) + text.value.substring(endIndex);
    } else {
      text.value = text.value.substring(0, startIndex) + '```\n' + selectedText + '\n```' + text.value.substring(endIndex);
    }
  }
  else if (selectedText.startsWith('`') && selectedText.endsWith('`') && selectedText.length > 1) {
    text.value = text.value.substring(0, startIndex) + selectedText.slice(1, -1) + text.value.substring(endIndex);
  }
  else {
    text.value = text.value.substring(0, startIndex) + '`' + selectedText + '`' + text.value.substring(endIndex);
  }
}

// Bulleted / numbered lists for Comment Text - every line needs its own
// marker for mistletoe to render it as a real multi-item list, unlike
// Bold/Italic/Quote which only ever wrap the whole selection once.

makeBulletList = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
    return;
  }

  var lines = selectedText.split('\n');
  var alreadyList = lines.every(function (line) { return line.startsWith('* '); });
  var newLines = alreadyList
    ? lines.map(function (line) { return line.slice(2); })
    : lines.map(function (line) { return '* ' + line; });

  text.value = text.value.substring(0, startIndex) + newLines.join('\n') + text.value.substring(endIndex);
}

makeNumberedList = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  if (selectedText.length == 0) {
    text.value = text.value.substring(0, startIndex) + selectedText + text.value.substring(endIndex);
    return;
  }

  var lines = selectedText.split('\n');
  var alreadyList = lines.every(function (line) { return /^\d+\.\s/.test(line); });
  var newLines = alreadyList
    ? lines.map(function (line) { return line.replace(/^\d+\.\s/, ''); })
    : lines.map(function (line, i) { return (i + 1) + '. ' + line; });

  text.value = text.value.substring(0, startIndex) + newLines.join('\n') + text.value.substring(endIndex);
}

// Insert a markdown link, prompting for the URL - selected text (if any)
// becomes the link label, otherwise the URL itself is used as the label.

makeLink = function (form) {
  var text = document.getElementById(form);
  var startIndex = text.selectionStart,
  endIndex = text.selectionEnd;
  var selectedText = text.value.substring(startIndex, endIndex);

  var url = window.prompt("Enter a URL", "https://");
  if (url === null) return;
  url = url.trim();
  if (!url) return;

  var label = selectedText.length ? selectedText : url;
  var markdown = '[' + label + '](' + url + ')';
  text.value = text.value.substring(0, startIndex) + markdown + text.value.substring(endIndex);
}

// Character Count

function charLimit(form, text) {

  var input = document.getElementById(form);

  var text = document.getElementById(text);

  var length = input.value.length;

  var maxLength = input.getAttribute("maxlength");

  if (length >= maxLength) {
    text.style.color = "#E53E3E";
  }
  else if (length >= maxLength * .72){
    text.style.color = "#FFC107";
  }
  else {
    text.style.color = "#A0AEC0";
  }

  text.innerText = maxLength - length;

}

// Reveal the collapsed body writing-space (and its toolbar) without
// stealing keyboard focus - used when overflow spills in while the
// user is still actively typing in the title field.
function revealBody() {
  var bodyWrite = document.getElementById("body-write");
  bodyWrite.classList.remove("d-none");
  bodyWrite.classList.add("collapsed");
  document.getElementById("add-body-link").classList.add("d-none");
}

// Manual "+ Add body text" affordance - reveals the body AND focuses it,
// since this is a deliberate user action to start typing there.
function openBody() {
  revealBody();
  document.getElementById("post-body").focus();
}

// Title/body overflow spillover: the title field has no native maxlength
// (so pasted text can trigger spillover too, unlike a maxlength attribute
// which would silently truncate pastes before any input event fires).
// Text typed past the 280-character title budget is pushed into the body
// field. titleOverflowBuffer tracks exactly what was most recently pushed,
// so backspacing back under 280 pulls it back live - but the moment the
// body's content no longer starts with that buffer (the user has actually
// edited the body themselves), syncing stops permanently rather than
// clobbering their own writing.
var titleOverflowBuffer = null;
var TITLE_MAX = 280;

function syncTitleOverflow() {

  var title = document.getElementById("post-title");
  var body = document.getElementById("post-body");
  var t = title.value;

  if (t.length > TITLE_MAX) {

    var stillSynced = titleOverflowBuffer !== null && body.value.startsWith(titleOverflowBuffer);
    // Each keystroke past the boundary only pushes the single newly-typed
    // character out of the title, but that character belongs *after* the
    // overflow already sitting in the body - so it has to be appended to
    // the existing buffer, not treated as the whole overflow on its own,
    // or repeated keystrokes would just clobber each other one at a time.
    var overflow = stillSynced ? titleOverflowBuffer + t.slice(TITLE_MAX) : t.slice(TITLE_MAX);
    title.value = t.slice(0, TITLE_MAX);

    if (stillSynced) {
      body.value = overflow + body.value.slice(titleOverflowBuffer.length);
    } else {
      body.value = overflow + body.value;
    }
    titleOverflowBuffer = overflow;
    revealBody();

  } else if (titleOverflowBuffer !== null) {

    if (body.value.startsWith(titleOverflowBuffer)) {
      var room = TITLE_MAX - t.length;
      var pullBack = titleOverflowBuffer.slice(0, room);
      var remaining = titleOverflowBuffer.slice(room);
      title.value = t + pullBack;
      body.value = remaining + body.value.slice(titleOverflowBuffer.length);
      titleOverflowBuffer = remaining.length ? remaining : null;
    } else {
      titleOverflowBuffer = null;
    }

  }

  charLimit("post-body", "character-count-body");
  document.getElementById("character-count-title").innerText = TITLE_MAX - title.value.length;

}

// Attach-a-link toolbar action for the title. Validation happens here in
// JS (not via native constraint validation) since #post-URL is a
// display:none field. Exactly one URL is enforced structurally - there is
// only one url-named field, and the UI toggles between the "Add link"
// button and the chip, never both.
function attachLink() {
  var current = document.getElementById("post-URL").value;
  var input = window.prompt("Attach a link to this post", current || "https://");
  if (input === null) return;
  input = input.trim();
  if (!input) return;
  var parsed;
  try {
    parsed = new URL(input);
  } catch (e) {
    alert("Please enter a valid URL (including http:// or https://).");
    return;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    alert("Links must start with http:// or https://.");
    return;
  }
  document.getElementById("post-URL").value = parsed.href;
  showAttachedLinkChip(parsed.href);
  hide_image();
  checkForRequired();
  autoSuggestTitle();
}

function removeAttachedLink() {
  document.getElementById("post-URL").value = "";
  document.getElementById("title-link-chip").classList.add("d-none");
  document.getElementById("title-link-btn").classList.remove("d-none");
  hide_image();
  checkForRequired();
}

function showAttachedLinkChip(url) {
  document.getElementById("title-link-chip-text").textContent = url;
  document.getElementById("title-link-chip").classList.remove("d-none");
  document.getElementById("title-link-btn").classList.add("d-none");
}

// Mobile bottom navigation bar

window.onload = function () {
  var prevScrollpos = window.pageYOffset;
  window.onscroll = function () {
    var currentScrollPos = window.pageYOffset;

    var topBar = document.getElementById("fixed-bar-mobile");

    var bottomBar = document.getElementById("mobile-bottom-navigation-bar");

    var dropdown = document.getElementById("mobileSortDropdown");

    var navbar = document.getElementById("navbar");

    if (bottomBar != null) {
      if (prevScrollpos > currentScrollPos && (window.innerHeight + currentScrollPos) < (document.body.offsetHeight - 65)) {
        bottomBar.style.bottom = "0px";
      } 
      else if (currentScrollPos <= 125 && (window.innerHeight + currentScrollPos) < (document.body.offsetHeight - 65)) {
        bottomBar.style.bottom = "0px";
      }
      else if (prevScrollpos > currentScrollPos && (window.innerHeight + currentScrollPos) >= (document.body.offsetHeight - 65)) {
        bottomBar.style.bottom = "-50px";
      }
      else {
        bottomBar.style.bottom = "-50px";
      }
    }

  // Execute if bottomBar exists

  if (topBar != null && dropdown != null) {
    if (prevScrollpos > currentScrollPos) {
      topBar.style.top = "49px";
      navbar.classList.remove("shadow");
    } 
    else if (currentScrollPos <= 125) {
      topBar.style.top = "49px";
      navbar.classList.remove("shadow");
    }
    else {
      topBar.style.top = "-49px";
      dropdown.classList.remove('show');
      navbar.classList.add("shadow");
    }
  }
  prevScrollpos = currentScrollPos;
}
}

// Tooltips

$(document).ready(function(){
  $('[data-toggle="tooltip"]').tooltip(); 
});

// Paste to create submission

document.addEventListener('paste', function (event) {

  var nothingFocused = document.activeElement === document.body;

  if (nothingFocused) {

    if (document.getElementById('guild-name-reference')) {
      var guild = document.getElementById('guild-name-reference').innerText;
    }

    var clipText = event.clipboardData.getData('Text');

    var url = new RegExp('^(?:[a-z]+:)?//', 'i');

    if (url.test(clipText) && window.location.pathname !== '/submit' && guild == undefined) {
      window.location.href = '/submit?url=' + clipText;
    }
    else if (url.test(clipText) && window.location.pathname !== '/submit' && guild !== undefined) {
      window.location.href = '/submit?url=' + clipText + '&guild=' + guild;
    }
    else if (url.test(clipText) && window.location.pathname == '/submit' && guild == undefined) {

      document.getElementById("post-URL").value = clipText;
      showAttachedLinkChip(clipText);
      hide_image();

      autoSuggestTitle()

    }
  }
});

//  Submit Page Front-end Validation

function checkForRequired() {

// A title alone is a complete, valid post - body/url/image are all
// optional additions on top of it, not requirements.

var title = document.getElementById("post-title");

var button = document.getElementById("create_button");

button.disabled = !title.checkValidity();

}

// Auto-suggest title given URL - only fills the title if it's still empty,
// so it never clobbers deliberately-typed text.

function autoSuggestTitle()  {

  var urlField = document.getElementById("post-URL");

  var titleField = document.getElementById("post-title");

  if (urlField.value.length > 0 && titleField.value.trim() === "") {

    var x = new XMLHttpRequest();
    x.withCredentials=true;
    x.onreadystatechange = function() {
      if (x.readyState == 4 && x.status == 200) {

        titleField.value = JSON.parse(x.responseText)["title"].slice(0, TITLE_MAX);

        syncTitleOverflow();
        checkForRequired()
      }
    }
    x.open('get','/submit/title?url=' + encodeURIComponent(urlField.value));
    x.send(null);

  };

};

// Exile Member

function exile_from_guild(boardname) {

  var exileForm = document.getElementById("exile-form");

  var exileError = document.getElementById("toast-error-message");

  var usernameField = document.getElementById("exile-username");

  var isValidUsername = usernameField.checkValidity();

  username = usernameField.value;

  if (isValidUsername) {

    var xhr = new XMLHttpRequest();
    xhr.open("post", "/mod/exile/"+boardname);
    xhr.withCredentials=true;
    f=new FormData();
    f.append("username", username);
    f.append("formkey", formkey());
    xhr.onload=function(){
      if (xhr.status==204) {
        window.location.reload(true);
      }
      else {
        $('#toast-exile-error').toast('dispose');
        $('#toast-exile-error').toast('show');
        exileError.textContent = JSON.parse(xhr.response)["error"];
      }
    }
    xhr.send(f)
  }

}

// Approve user
function approve_from_guild(boardid) {

  var approvalForm = document.getElementById("approve-form");

  var approveError = document.getElementById("toast-error-message");

  var usernameField = document.getElementById("approve-username");

  var isValidUsername = usernameField.checkValidity();

  username = usernameField.value;

  if (isValidUsername) {

    var xhr = new XMLHttpRequest();
    xhr.open("post", "/mod/approve/"+boardid);
    xhr.withCredentials=true;
    f=new FormData();
    f.append("username", username);
    f.append("formkey", formkey());
    xhr.onload=function(){
      if (xhr.status==204) {
        window.location.reload(true);
      }
      else {
        $('#toast-approve-error').toast('dispose');
        $('#toast-approve-error').toast('show');
        approveError.textContent = JSON.parse(xhr.response)["error"];
      }
    }
    xhr.send(f)
  }

}

// Invite user to mod
function invite_mod_to_guild(boardid) {

  var inviteForm = document.getElementById("invite-form");

  var inviteError = document.getElementById("toast-error-message");

  var usernameField = document.getElementById("invite-username");

  var isValidUsername = usernameField.checkValidity();

  username = usernameField.value;

  if (isValidUsername) {

    var xhr = new XMLHttpRequest();
    xhr.open("post", "/mod/invite_mod/"+boardid);
    xhr.withCredentials=true;
    f=new FormData();
    f.append("username", username);
    f.append("formkey", formkey());
    xhr.onload=function(){
      if (xhr.status==204) {
        window.location.reload(true);
      }
      else {
        $('#toast-invite-error').toast('dispose');
        $('#toast-invite-error').toast('show');
        inviteError.textContent = JSON.parse(xhr.response)["error"];
      }
    }
    xhr.send(f)
  }

}

block_user=function() {

  var exileForm = document.getElementById("exile-form");

  var exileError = document.getElementById("toast-error-message");

  var usernameField = document.getElementById("exile-username");

  var isValidUsername = usernameField.checkValidity();

  username = usernameField.value;

  if (isValidUsername) {

    var xhr = new XMLHttpRequest();
    xhr.open("post", "/settings/block");
    xhr.withCredentials=true;
    f=new FormData();
    f.append("username", username);
    f.append("formkey", formkey());
    xhr.onload=function(){
      if (xhr.status<300) {
        window.location.reload(true);
      }
      else {
        $('#toast-exile-error').toast('dispose');
        $('#toast-exile-error').toast('show');
        exileError.textContent = JSON.parse(xhr.response)["error"];
      }
    }
    xhr.send(f)
  }

}

post_comment=function(fullname){


  var form = new FormData();

  form.append('formkey', formkey());
  form.append('parent_fullname', fullname);
  form.append('submission', document.getElementById('reply-form-submission-'+fullname).value);
  form.append('body', document.getElementById('reply-form-body-'+fullname).value);
  form.append('file', document.getElementById('file-upload-reply-'+fullname).files[0]);
  var sensitiveBox = document.getElementById('reply-sensitive-'+fullname);
  form.append('sensitive', (sensitiveBox && sensitiveBox.checked) ? 'true' : '');


  var xhr = new XMLHttpRequest();
  xhr.open("post", "/api/comment");
  xhr.withCredentials=true;
  xhr.onload=function(){
    if (xhr.status==200) {
      commentForm=document.getElementById('comment-form-space-'+fullname);
      commentForm.innerHTML=JSON.parse(xhr.response)["html"];
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-success').toast('show');
    }
    else {
      var commentError = document.getElementById("comment-error-text");
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-error').toast('show');
      commentError.textContent = JSON.parse(xhr.response)["error"];
    }
  }
  xhr.send(form)

  document.getElementById('save-reply-to-'+fullname).classList.add('disabled');

}

herald_comment=function(name,cid){


  var xhr = new XMLHttpRequest();
  xhr.open("post", "/mod/distinguish_comment/"+name+'/'+cid);

  var form = new FormData();

  form.append('formkey', formkey());

  xhr.withCredentials=true;
  xhr.onload=function(){
    if (xhr.status==200) {
      comment=document.getElementById('comment-'+cid+'-only');
      comment.innerHTML=JSON.parse(xhr.response)["html"];
    }
    else {
      var commentError = document.getElementById("comment-error-text");
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-error').toast('show');
      commentError.textContent = JSON.parse(xhr.response)["error"];
    }
  }
  xhr.send(form)

}

pin_comment=function(name,cid){


  var xhr = new XMLHttpRequest();
  xhr.open("post", "/mod/comment_pin/"+name+'/'+cid);

  var form = new FormData();

  form.append('formkey', formkey());

  xhr.withCredentials=true;
  xhr.onload=function(){
    if (xhr.status==200) {
      comment=document.getElementById('comment-'+cid+'-only');
      comment.innerHTML=JSON.parse(xhr.response)["html"];
    }
    else {
      var commentError = document.getElementById("comment-error-text");
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-error').toast('show');
      commentError.textContent = JSON.parse(xhr.response)["error"];
    }
  }
  xhr.send(form)

}



//part of submit page js

hide_image=function(){
  x=document.getElementById('image-upload-block');
  url=document.getElementById('post-URL').value;
  if (url.length>=1){
    x.classList.add('d-none');
  }
  else {
    x.classList.remove('d-none');
  }
}


comment_edit=function(id){

  var commentError = document.getElementById("comment-error-text");

  var form = new FormData();

  form.append('formkey', formkey());
  form.append('body', document.getElementById('comment-edit-body-'+id).value);


  var xhr = new XMLHttpRequest();
  xhr.open("post", "/edit_comment/"+id);
  xhr.withCredentials=true;
  xhr.onload=function(){
    if (xhr.status==200) {
      commentForm=document.getElementById('comment-text-'+id);
      commentForm.innerHTML=JSON.parse(xhr.response)["html"];
      document.getElementById('cancel-edit-'+id).click()
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-success').toast('show');
    }
    else {
      $('#toast-comment-success').toast('dispose');
      $('#toast-comment-error').toast('dispose');
      $('#toast-comment-error').toast('show');
      commentError.textContent = JSON.parse(xhr.response)["error"];
    }
  }
  xhr.send(form)

}


filter_guild=function() {

  var exileForm = document.getElementById("exile-form");

  var exileError = document.getElementById("toast-error-message");

  var boardField = document.getElementById("exile-username");

  var isValidUsername = boardField.checkValidity();

  boardname = boardField.value;

  if (isValidUsername) {

    var xhr = new XMLHttpRequest();
    xhr.open("post", "/settings/block_guild");
    xhr.withCredentials=true;
    f=new FormData();
    f.append("board", boardname);
    f.append("formkey", formkey());
    xhr.onload=function(){
      if (xhr.status<300) {
        window.location.reload(true);
      }
      else {
        $('#toast-exile-error').toast('dispose');
        $('#toast-exile-error').toast('show');
        exileError.textContent = JSON.parse(xhr.response)["error"];
      }
    }
    xhr.send(f)
  }

}

coin_quote = function() {

  var coins = document.getElementById('select-coins');
  var btn = document.getElementById('buy-coin-btn')
  var promo=document.getElementById('promo-code')
  var promotext=document.getElementById('promo-text')

  coin_count = coins.selectedOptions[0].value

  var xhr = new XMLHttpRequest();
  xhr.open('get', '/shop/get_price?coins='+coin_count+'&promo='+promo.value)

  xhr.onload=function(){
    var s = 'Buy '+ coin_count + ' Coin';

    if (coin_count > 1){s = s+'s'};

    s=s+': $'+JSON.parse(xhr.response)["price"];

    btn.value=s;

    promotext.innerText=JSON.parse(xhr.response)["promo"];
  }
  xhr.send()
}


var tipModal2 = function(id, content, link, recipient, recipientPFP) {
  console.log('opened modal, tipModal2 function triggered')

  document.getElementById('tip-recipient-pfp').src = recipientPFP;

  document.getElementById("tip-content-type").innerText = content
  document.getElementById("tip-recipient-username").innerText = recipient

  document.getElementById("sendTipButton").onclick = function() {
    post_toast('/gift_'+ content +'/' + id + '?coins=1',
      callback = function() {
        location.href = link
      }
      )
  }

  console.log(recipientPFP, id, content, link, recipient)
}

var togglecat = function(sort, reload=false, delay=1000, page="/all", inpage="/inpage/all") {
  var cbs = document.getElementsByClassName('cat-check');
  var l = []
  for (var i=0; i< cbs.length; i++) {
    l.push(cbs[i].checked)
  }
  setTimeout(function(){triggercat(sort, l, reload, page, inpage)}, delay)
  return l;
}

var triggercat=function(sort, cats, reload, page, inpage="/inpage/all") {

  var cbs = document.getElementsByClassName('cat-check');
  var l = []
  for (var i=0; i< cbs.length; i++) {
    l.push(cbs[i].checked)
  }



  for (var i=0; i<l.length; i++){
    if (cats[i] != l[i]){
      console.log("triggerfail");
      return false;
    }
  }

  console.log("triggercat")

  var catlist=[]
  for (var i=0; i< cbs.length; i++) {
    if(cbs[i].checked){
      catlist.push(cbs[i].dataset.cat);
    }
  }

  var groups = document.getElementsByClassName('cat-group');
  var grouplist=[];
  for (i=0; i<groups.length; i++){
    if(groups[i].checked){
      grouplist.push(groups[i].dataset.group);
    }
  }

  var url=inpage+'?sort='+ sort +'&cats=' + catlist.join(',') + '&groups=' + grouplist.join(',');
  

  xhr = new XMLHttpRequest();
  xhr.open('get', url);
  xhr.withCredentials=true;

  xhr.onload=function(){
    if (reload){
      document.location.href=page
    }
    else {
      var l = document.getElementById('posts');
      l.innerHTML=xhr.response;
      register_votes();
    }
  }
  xhr.send()
}


var permsEdit = function(username, permstring) {

  document.getElementById('permedit-user').innerText = username
  document.getElementById('edit-perm-username').value = username

  cbs = document.getElementsByClassName('perm-box')

  for (i=0; i< cbs.length; i++) {
    cbs[i].checked = permstring.includes(cbs[i].dataset.perm) || permstring.includes('full')
  }

}

var permfull=function() {

  cbs = document.getElementsByClassName('perm-box')

  full = cbs[0]

  if (full.checked) {
    for (i=1; i< cbs.length; i++) {
      cbs[i].checked = true;
    }
  }
}
var permother=function() {

  cbs = document.getElementsByClassName('perm-box')

  full = cbs[0]

  for (i=1; i< cbs.length; i++) {
    if(cbs[i].checked == false) {
      full.checked=false;
    }
  }
}

var cattoggle=function(id){

  var check = document.getElementById('group-'+id);

  check.click()

  var x=document.getElementsByClassName('group-'+id);
  for (i=0;i<x.length;i++) {
    x[i].checked=check.checked
  }

  card=document.getElementById('cat-card-'+id)
  card.classList.toggle('selected');
}

var all_cats=function(page, inpage="/inpage/all") {
  // "Show all" means "no category filter" (matches the empty-selection = no
  // filter semantics every other sidebar filter uses) - so this unchecks
  // everything and submits an explicit empty cats= list, rather than
  // checking every box and submitting all of them.
  var x=document.getElementsByClassName('cat-check');
  for(i=0;i<x.length;i++){
    x[i].checked=false;
  };

  var y=document.getElementsByClassName('cat-group');
  for(i=0;i<y.length;i++){
    y[i].checked=false;
  };

  togglecat('hot', reload=true, delay=0, page=page, inpage=inpage)
}

var filterSidebarList = function(input, listId) {
  var query = input.value.trim().toLowerCase();
  var list = document.getElementById(listId);
  if (!list) return;
  var rows = list.querySelectorAll('[data-filter-row]');
  var anyVisible = false;
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var text = (row.getAttribute('data-filter-text') || row.textContent || '').toLowerCase();
    var match = (query === '' || text.indexOf(query) !== -1);
    row.classList.toggle('d-none', !match);
    if (match) anyVisible = true;
  }
  var emptyEl = document.getElementById(listId + '-empty');
  if (emptyEl) emptyEl.classList.toggle('d-none', anyVisible);
};

var resortSidebarFilterList = function(listId) {
  var list = document.getElementById(listId);
  if (!list) return;
  var rows = Array.prototype.slice.call(list.querySelectorAll('[data-filter-row]'));
  rows.sort(function(a, b) {
    var aSel = a.classList.contains('is-selected');
    var bSel = b.classList.contains('is-selected');
    if (aSel !== bSel) return aSel ? -1 : 1;
    var aText = (a.getAttribute('data-filter-text') || '').toLowerCase();
    var bText = (b.getAttribute('data-filter-text') || '').toLowerCase();
    return aText < bText ? -1 : (aText > bText ? 1 : 0);
  });
  rows.forEach(function(row) { list.appendChild(row); });
};

var updateSidebarFilterCount = function(listId) {
  var list = document.getElementById(listId);
  var countEl = document.getElementById(listId + '-count');
  if (!list || !countEl) return;
  var n = list.querySelectorAll('[data-filter-row].is-selected').length;
  countEl.textContent = n + ' selected';
  countEl.classList.toggle('d-none', n === 0);
};

var toggleSidebarFilterOption = function(li, listId) {
  var selected = li.classList.toggle('is-selected');
  var link = li.querySelector('a');
  var icon = li.querySelector('i');
  if (link) link.classList.toggle('text-purple', selected);
  if (link) link.classList.toggle('font-weight-bold', selected);
  if (link) link.classList.toggle('text-black', !selected);
  if (icon) icon.classList.toggle('invisible', !selected);
  resortSidebarFilterList(listId);
  updateSidebarFilterCount(listId);
};

var deselectAllSidebarFilter = function(listId) {
  var list = document.getElementById(listId);
  if (!list) return;
  var rows = list.querySelectorAll('[data-filter-row].is-selected');
  for (var i = 0; i < rows.length; i++) {
    var li = rows[i];
    li.classList.remove('is-selected');
    var link = li.querySelector('a');
    var icon = li.querySelector('i');
    if (link) { link.classList.remove('text-purple', 'font-weight-bold'); link.classList.add('text-black'); }
    if (icon) icon.classList.add('invisible');
  }
  resortSidebarFilterList(listId);
  updateSidebarFilterCount(listId);
};

var applySidebarFilter = function(listId, baseUrl, paramName) {
  var list = document.getElementById(listId);
  if (!list) return;
  var values = Array.prototype.map.call(
    list.querySelectorAll('[data-filter-row].is-selected'),
    function(row) { return row.getAttribute('data-value'); }
  );
  // Always navigate, even with zero selected - an explicit empty param is
  // how the backend knows "clear this filter" rather than "leave it as-is".
  window.location.href = baseUrl + '?' + paramName + '=' + values.join(',');
};

var applyCurationFilter = function(listId, hiddenInputId, formId) {
  // Curation-filter variant of applySidebarFilter: this mutates a shared,
  // owner-only resource rather than the viewer's own session preference, so
  // it submits a real formkey-protected POST instead of a plain GET.
  var list = document.getElementById(listId);
  var hidden = document.getElementById(hiddenInputId);
  var form = document.getElementById(formId);
  if (!list || !hidden || !form) return;
  var values = Array.prototype.map.call(
    list.querySelectorAll('[data-filter-row].is-selected'),
    function(row) { return row.getAttribute('data-value'); }
  );
  hidden.value = values.join(',');
  form.submit();
};

var startSidebarFilterMarquee = function(el) {
  var overflow = el.scrollWidth - el.clientWidth;
  if (overflow > 2) {
    el.style.textOverflow = 'clip';
    el.style.transitionDuration = Math.max(1.2, overflow / 40) + 's';
    el.style.transform = 'translateX(-' + overflow + 'px)';
  }
};

var stopSidebarFilterMarquee = function(el) {
  el.style.transitionDuration = '0s';
  el.style.transform = 'translateX(0)';
  el.style.textOverflow = 'ellipsis';
};


//mobile prompt
if (("standalone" in window.navigator) &&       // Check if "standalone" property exists
    window.navigator.standalone){               // Test if using standalone navigator

    // Web page is loaded via app mode (full-screen mode)
    // (window.navigator.standalone is TRUE if user accesses website via App Mode)

} else {
  if (window.innerWidth <= 737){
    try {
      $('#mobile-prompt').tooltip('show')
      $('.tooltip')[0].addEventListener(
        'click',
        function(event){
          $('#mobile-prompt').tooltip('hide')
          var xhr = new XMLHttpRequest();
          xhr.withCredentials=true;
          xhr.open("POST", '/dismiss_mobile_tip', true);
          xhr.send();
        }
      )
    } catch (error) {
      console.error(error);
    }
  }
}

$('.mention-user').click(function (event) {

  if (event.which != 1) {
    return
  }

  event.preventDefault();

  window.location.href='/@' + $(this).data('original-name');

});

$('.expandable-image').click( function(event) {

  if (event.which != 1) {
    return
  }
  event.preventDefault();

  var url= $(this).data('url');

  expandDesktopImage(url);
})

$('.text-expand').click(function(event){
  if (event.which != 1) {
    return
  };
  id=$(this).data('id');


  $('#post-text-'+id).toggleClass('d-none');
  $('.text-expand-label-'+id).toggleClass('d-none');

})



function mod_post(url, type, id) {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", url, true);
        var form = new FormData()
        form.append("formkey", formkey());
        item=document.getElementById(type);
        button=document.getElementById(id);
        if (item.type=="checkbox") {
          form.append(item.name, item.checked)
          if (item.checked) {
            form.append(item.name, true);
          } else {
            form.append(item.name, false);
          }
        }
        else {
          form.append(item.name, item.value);
        }
        xhr.withCredentials=true;
        xhr.onprogress=function(){
          button.classList.add("btn-primary");
          button.disabled = true;
          button.innerHTML = '<span class="spinner-border spinner-border-sm mr-2" role="status" aria-hidden="true"></span>Saving';
        }
        xhr.onload=function(){
          if (xhr.status >= 400)
          {
            button.classList.add("btn-primary");
            button.disabled=false;
            button.innerHTML="Save"
            data=JSON.parse(xhr.response);
            $('#toast-post-error').toast('dispose');
            $('#toast-post-error').toast('show');
            document.getElementById('toast-post-error-text').innerText = data["error"];
            return;
          }
          button.classList.remove("btn-danger");
          button.classList.add("btn-success");
          button.innerHTML = `<i class="fas fa-check mr-2"></i>Saved`;
        }
      xhr.send(form);
    }
